"""Stage 5 tests: rank, pipeline failures, outputs, CLI. Synthetic sample and mock client only."""
import json
from pathlib import Path

import pytest

from promofinder import runner
from promofinder.llm import MockClient
from promofinder.llm.client import LLMError
from promofinder.models import JudgedCandidate, ProposedCandidate as PC, RubricScores, ValidatedCandidate
from promofinder.parser import parse_text
from promofinder.pipeline import compute_prompt_hash, process_episode, recap_set, run_episodes
from promofinder.rank import GATE_MIN, overlap_fraction, passes_gate, rank_candidates, weighted_score
from promofinder.report import write_outputs

SAMPLE = Path(__file__).resolve().parent.parent / "sample" / "episode-001-sample.txt"


@pytest.fixture(scope="module")
def ep():
    return parse_text(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name)


def sc(h=3, c=3, s=3, e=3, d=3):
    return RubricScores(hook=h, cliffhanger=c, standalone=s, escalation=e, density=d)


def jc(ep_id, n, start, end, scores):
    c = ValidatedCandidate(candidate_id=f"{ep_id}-C{n:02d}", start_id="a", end_id="b", hook_id="a", cliffhanger_id="b", start_s=start, end_s=end,
                           duration_s=end - start, hook_quote="h", cliffhanger_quote="c", unit_ids=["a"])
    return JudgedCandidate(candidate=c, scores=scores, reason="r", evidence_unit_ids=[])


def status(entries):
    return {e.judged.candidate.candidate_id: (e.status, e.rank) for e in entries}


# ---- rank ----------------------------------------------------------------------------------
def test_gate_and_weights():
    assert GATE_MIN == 2 and passes_gate(sc(2, 2, 2)) and not passes_gate(sc(4, 1, 4)) and not passes_gate(sc(4, 4, 1))
    assert weighted_score(sc(4, 2, 2, 0, 4)) == round(1.2 + 0.6 + 0.4 + 0, 3) == 2.2


def test_rank_order_density_tiebreak_and_gate():
    j = [jc("E001", 1, 0, 30, sc(3, 3, 3, 3, 1)), jc("E002", 1, 0, 30, sc(3, 3, 3, 3, 4)), jc("E003", 1, 0, 30, sc(4, 1, 4, 4, 4))]
    r = status(rank_candidates(j))
    assert r["E002-C01"] == ("ranked", 1) and r["E001-C01"] == ("ranked", 2) and r["E003-C01"] == ("below_gate", None)


def test_overlap_over_30_percent_is_dropped_and_small_overlap_kept():
    a, b, c = jc("E001", 1, 0, 40, sc(4, 4, 4, 4)), jc("E001", 2, 10, 50, sc(3, 3, 3, 3)), jc("E001", 3, 36, 76, sc(2, 2, 2, 2))
    assert overlap_fraction(a.candidate, b.candidate) == 0.75 and overlap_fraction(a.candidate, c.candidate) == 0.1
    r = status(rank_candidates([a, b, c]))
    assert r["E001-C02"][0] == "dropped_overlap" and r["E001-C01"][0] == "ranked" and r["E001-C03"][0] == "ranked"


def test_per_episode_cap_and_global_top_n():
    j = [jc("E001", i, i * 100, i * 100 + 30, sc(4 - i + 1, 4, 4, 4)) for i in (1, 2, 3)]
    assert status(rank_candidates(j))["E001-C03"][0] == "dropped_cap"
    many = [jc(f"E{n:03d}", 1, 0, 30, sc(3, 3, 3, 3, n % 5)) for n in range(1, 21)]
    ents = rank_candidates(many, top_n=15)
    assert sum(e.status == "ranked" for e in ents) == 15 and sum(e.status == "not_in_top_n" for e in ents) == 5
    assert [e.rank for e in ents if e.status == "ranked"] == list(range(1, 16))


def test_recap_flag_demotes_but_keeps():
    j = [jc("E001", 1, 0, 30, sc(4, 4, 4, 4)), jc("E002", 1, 0, 30, sc(3, 3, 3, 3))]
    ents = rank_candidates(j, recap={"E001-C01"})
    assert status(ents)["E002-C01"] == ("ranked", 1) and status(ents)["E001-C01"] == ("ranked", 2)
    assert next(e for e in ents if e.rank == 2).recap


# ---- pipeline ------------------------------------------------------------------------------
PROPOSAL = {"candidates": [{"start_id": "E001-S003", "end_id": "E001-A028", "hook_id": "E001-U006", "cliffhanger_id": "E001-A028"},
                           {"start_id": "E001-S009", "end_id": "E001-A028", "hook_id": "E001-S009", "cliffhanger_id": "E001-A028"}]}


def judgment(cid, v=3):
    return {"candidate_id": cid, "scores": {"hook": v, "cliffhanger": v, "standalone": v, "escalation": v, "density": v}, "reason": "ok", "evidence_unit_ids": []}


def test_full_episode_with_mock_logs_rejection_and_judges_valid(ep):
    res = process_episode(ep, MockClient([PROPOSAL, {"judgments": [judgment("E001-C01")]}]))
    assert len(res.judged) == 1 and [f.kind for f in res.failures] == ["rejected_candidate"] and "inside utterance" in res.failures[0].detail


def test_no_promo_is_not_a_failure(ep):
    res = process_episode(ep, MockClient([{"candidates": [], "no_promo_reason": "none"}]))
    assert res.judged == [] and res.failures == []


def test_judge_errors_become_failures_not_crashes(ep):
    p = {"candidates": PROPOSAL["candidates"][:1]}
    wrong_count = process_episode(ep, MockClient([p, {"judgments": []}]))
    assert [f.kind for f in wrong_count.failures] == ["judge_error"] and wrong_count.judged == []
    bad_evidence = dict(judgment("E001-C01"), evidence_unit_ids=["E001-S043"])
    assert process_episode(ep, MockClient([p, {"judgments": [bad_evidence]}])).failures[0].stage == "judge"
    invalid = process_episode(ep, MockClient([p, {"judgments": [{"candidate_id": "E001-C01"}]}]))
    assert invalid.failures[0].stage == "judge"


class Boom:
    def complete_json(self, **kw):
        raise LLMError("HTTP 401: bad key")


class Crash:
    def complete_json(self, **kw):
        raise KeyError("bug")


def test_llm_error_and_unexpected_error_are_logged_per_episode(ep):
    r = run_episodes([ep], Boom(), log=lambda s: None)[0]
    assert r.failures[0].stage == "propose" and "401" in r.failures[0].detail
    r2 = run_episodes([ep, ep], Crash(), log=lambda s: None)
    assert len(r2) == 2 and r2[0].failures[0].kind == "unexpected_error"


def test_prompt_hash_is_stable_and_12_chars():
    assert compute_prompt_hash() == compute_prompt_hash() and len(compute_prompt_hash()) == 12


def test_recap_set_needs_adjacent_episode(ep):
    other = ep.model_copy(update={"episode_id": "E002", "episode_number": 2})
    res = [process_episode(other, MockClient([{"candidates": [PROPOSAL["candidates"][0] | {"start_id": "E001-S003"}]}, {"judgments": []}]))]
    assert recap_set([ep, other], res) == set()       # nothing judged, so nothing to flag; must not crash


# ---- outputs -------------------------------------------------------------------------------
def meta(mock=False):
    return {"run_id": "run-test", "provider": "groq", "model": "m", "is_mock": mock, "prompt_hash": "abc", "episodes": 1}


def test_write_outputs_records_model_hash_run_and_keeps_failures_separate(ep, tmp_path):
    res = run_episodes([ep], MockClient([PROPOSAL, {"judgments": [judgment("E001-C01")]}]), log=lambda s: None)
    ents = rank_candidates([j for r in res for j in r.judged])
    fails = write_outputs(tmp_path, ents, res, [], meta())
    promos = json.loads((tmp_path / "promos.json").read_text(encoding="utf-8"))
    assert len(promos) == 1 and promos[0]["rank"] == 1 and promos[0]["model"] == "m" and promos[0]["prompt_hash"] == "abc" and promos[0]["run_id"] == "run-test"
    assert promos[0]["segments"][0]["start_unit"] == "E001-S003" and promos[0]["join_type"] == "none"
    assert promos[0]["hook"]["text"] == next(u.text for u in ep.units if u.unit_id == "E001-U006")
    assert len(fails) == 1 and json.loads((tmp_path / "failures.json").read_text(encoding="utf-8"))[0]["kind"] == "rejected_candidate"
    assert (tmp_path / "raw" / "E001.json").exists() and (tmp_path / "report.html").exists() and not (tmp_path / "MOCK_RUN.txt").exists()
    assert "MOCK RUN" not in (tmp_path / "report.html").read_text(encoding="utf-8")


def test_mock_outputs_are_labelled(ep, tmp_path):
    write_outputs(tmp_path, [], [], [], meta(mock=True))
    assert (tmp_path / "MOCK_RUN.txt").exists() and "MOCK RUN" in (tmp_path / "report.html").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))["is_mock"] is True


# ---- CLI -----------------------------------------------------------------------------------
def test_cli_mock_run_on_sample(tmp_path, capsys):
    assert runner.main(["--input", str(SAMPLE), "--provider", "mock", "--out", str(tmp_path / "o")]) == 0
    assert "MOCK RUN" in capsys.readouterr().out and (tmp_path / "o" / "MOCK_RUN.txt").exists()
    assert (tmp_path / "o" / "audit" / "audit_report.txt").exists()


def test_cli_real_provider_without_key_stops(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)                      # no .env here
    cfg = Path(__file__).resolve().parent.parent / "config.yaml"
    assert runner.main(["--input", str(SAMPLE), "--config", str(cfg), "--out", str(tmp_path / "o")]) == 2
    assert "GROQ_API_KEY" in capsys.readouterr().err and not (tmp_path / "o").exists()


def test_cli_only_filter_unknown_id(tmp_path, capsys):
    assert runner.main(["--input", str(SAMPLE), "--provider", "mock", "--only", "E099", "--out", str(tmp_path / "o")]) == 2
