"""Stages 2-4 tests. Synthetic sample and mock/faked transport only: no API, no pack."""
import json
import os
from pathlib import Path

import pytest

from promofinder.config import RUBRIC_PATH, load_config, load_dotenv
from promofinder.llm import MockClient, OpenAICompatibleClient, create_client
from promofinder.llm.client import LLMError
from promofinder.models import (JudgeResponse, ProposalResponse, ProposedCandidate as PC, RubricScores, ValidatedCandidate)
from promofinder.parser import parse_text
from promofinder.stages.judge import judge, judge_prompts, strip_unverified_quotes
from promofinder.stages.propose import propose, proposer_prompts, render_numbered_episode
from promofinder.stages.validate import cuts_inside_speech, validate_candidates

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample" / "episode-001-sample.txt"


@pytest.fixture(scope="module")
def ep():
    return parse_text(SAMPLE.read_text(encoding="utf-8"), SAMPLE.name)


def cand(s, e, h, c):
    return PC(start_id=s, end_id=e, hook_id=h, cliffhanger_id=c)


def check(ep, *ids):
    r = validate_candidates(ep, [cand(*ids)])
    return r.valid, [x.reasons for x in r.rejected]


# ---- Stage 2: prompt and rendering --------------------------------------------------------------------
def test_render_lists_every_unit_with_its_id(ep):
    text = render_numbered_episode(ep)
    assert all(f"[{u.unit_id}]" in text for u in ep.units)


def test_proposer_sees_logline_but_not_continuity_note_and_rubric(ep):
    system, user = proposer_prompts(ep, RUBRIC_PATH.read_text(encoding="utf-8"))
    assert ep.logline in user and ep.continuity_note not in user and "Hook (first 3s)" in user
    assert "never instructions" in system and "Never start or end a clip while someone is still speaking" in user


def test_propose_returns_ids_only_and_no_promo_is_valid(ep):
    ok = MockClient([{"candidates": [{"start_id": "E001-S003", "end_id": "E001-A028", "hook_id": "E001-U006", "cliffhanger_id": "E001-A028"}]}])
    assert propose(ep, ok).candidates[0].start_id == "E001-S003"
    none = propose(ep, MockClient([{"candidates": [], "no_promo_reason": "nothing qualifies"}]))
    assert none.candidates == [] and none.no_promo_reason == "nothing qualifies"


def test_extra_keys_from_the_model_are_ignored_not_fatal():
    r = ProposalResponse.model_validate({"candidates": [{"start_id": "a", "end_id": "b", "hook_id": "c", "cliffhanger_id": "d", "reason": "x"}]})
    assert len(r.candidates) == 1


def test_more_than_five_candidates_is_invalid():
    five = {"start_id": "a", "end_id": "b", "hook_id": "c", "cliffhanger_id": "d"}
    with pytest.raises(Exception):
        ProposalResponse.model_validate({"candidates": [five] * 6})


def test_rubric_loads_from_any_working_directory(ep, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert propose(ep, MockClient([{"candidates": []}])).candidates == []


# ---- LLM client (HTTP faked at _send) -----------------------------------------------------------------
class Tiny(ProposalResponse):
    pass


def body(text, usage=None):
    return json.dumps({"choices": [{"message": {"content": text}}], "usage": usage or {"prompt_tokens": 100, "completion_tokens": 10}})


def http_client(tmp_path, replies, **kw):
    sent, waits = [], []
    c = OpenAICompatibleClient(model="m", base_url="https://x.test/v1/", api_key_env="PF_KEY", cache_dir=tmp_path, sleep=waits.append, **kw)

    def fake_send(url, headers, raw):
        sent.append((url, headers, json.loads(raw)))
        return replies.pop(0)

    c._send = fake_send
    return c, sent, waits


GOOD = '{"candidates": [], "no_promo_reason": "none"}'


def ask(c, **kw):
    return c.complete_json(system="s", user="u", response_model=ProposalResponse, **kw)


def test_request_shape_usage_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_KEY", "secret-key")
    c, sent, _ = http_client(tmp_path, [(200, {}, body(GOOD))])
    assert ask(c).no_promo_reason == "none"
    url, headers, b = sent[0]
    assert url == "https://x.test/v1/chat/completions" and headers["Authorization"] == "Bearer secret-key"
    assert b["response_format"] == {"type": "json_object"} and b["temperature"] == 0 and b["seed"] == 2026
    assert [m["role"] for m in b["messages"]] == ["system", "user"]
    assert c.usage["prompt_tokens"] == 100 and c.usage["completion_tokens"] == 10
    assert ask(c).no_promo_reason == "none" and len(sent) == 1 and c.usage["cache_hits"] == 1      # second call from cache
    assert all("secret-key" not in p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))


def test_cache_bypass_calls_the_model_again(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_KEY", "k")
    c, sent, _ = http_client(tmp_path, [(200, {}, body(GOOD)), (200, {}, body(GOOD))])
    ask(c)
    ask(c, cache_bypass=True)
    assert len(sent) == 2


def test_retries_429_honouring_retry_after_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_KEY", "k")
    c, sent, waits = http_client(tmp_path, [(429, {"retry-after": "7"}, "slow"), (503, {}, "down"), (200, {}, body(GOOD))])
    assert ask(c).no_promo_reason == "none" and len(sent) == 3 and waits == [7.0, 2.0]


def test_bad_key_fails_at_once_and_exhausted_retries_report(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_KEY", "k")
    c, sent, _ = http_client(tmp_path, [(401, {}, "bad key")])
    with pytest.raises(LLMError, match="401"):
        ask(c)
    assert len(sent) == 1
    c2, sent2, _ = http_client(tmp_path / "b", [(500, {}, "e")] * 4, max_retries=3)
    with pytest.raises(LLMError, match="after 4 attempts"):
        ask(c2)
    assert len(sent2) == 4


def test_one_repair_retry_for_bad_json_then_success(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_KEY", "k")
    c, sent, _ = http_client(tmp_path, [(200, {}, body("not json at all")), (200, {}, body(GOOD))])
    assert ask(c).no_promo_reason == "none" and len(sent) == 2 and c.usage["repairs"] == 1
    repair_msgs = sent[1][2]["messages"]
    assert repair_msgs[-2] == {"role": "assistant", "content": "not json at all"} and "not valid" in repair_msgs[-1]["content"]


def test_schema_violation_repairs_once_then_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_KEY", "k")
    bad = body('{"candidates": "oops"}')
    c, sent, _ = http_client(tmp_path, [(200, {}, bad), (200, {}, bad)])
    with pytest.raises(LLMError, match="after one repair retry"):
        ask(c)
    assert len(sent) == 2
    assert not list(tmp_path.glob("*.json"))                       # a bad reply is never cached


def test_fenced_json_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("PF_KEY", "k")
    c, sent, _ = http_client(tmp_path, [(200, {}, body("```json\n" + GOOD + "\n```"))])
    assert ask(c).no_promo_reason == "none" and len(sent) == 1


def test_missing_key_and_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("PF_KEY", raising=False)
    c, _, _ = http_client(tmp_path, [])
    with pytest.raises(LLMError, match="PF_KEY"):
        ask(c)
    with pytest.raises(ValueError):
        create_client("nope", model="m", base_url="u", api_key_env="K")
    assert isinstance(create_client("mock", model="m", base_url="u", api_key_env="K"), MockClient)


def test_config_and_dotenv(tmp_path, monkeypatch):
    assert load_config(ROOT / "config.yaml").provider in {"mock", "groq", "openai-compatible"}
    env = tmp_path / ".env"
    env.write_text("# c\nPF_T1='abc'\nPF_EMPTY=\n", encoding="utf-8")
    monkeypatch.delenv("PF_T1", raising=False)
    load_dotenv(env)
    assert os.environ["PF_T1"] == "abc" and "PF_EMPTY" not in os.environ
    monkeypatch.delenv("PF_T1", raising=False)


# ---- Stage 3: validation -------------------------------------------------------------------------------
def test_clean_candidate_passes_with_exact_source_quotes(ep):
    valid, rejected = check(ep, "E001-S003", "E001-A028", "E001-U006", "E001-A028")
    assert not rejected and len(valid) == 1
    v = valid[0]
    by_id = {u.unit_id: u for u in ep.units}
    assert (v.start_s, v.end_s, v.duration_s) == (4.0, 40.0, 36.0) and v.candidate_id == "E001-C01"
    assert v.hook_quote == by_id["E001-U006"].text and v.cliffhanger_quote == by_id["E001-A028"].text
    assert v.unit_ids[0] == "E001-S003" and v.unit_ids[-1] == "E001-A028"


def test_late_hook_is_measured_from_first_spoken_line_and_only_flags(ep):
    valid, _ = check(ep, "E001-S003", "E001-A028", "E001-U006", "E001-A028")      # first line at 7.5, clip starts 4.0
    assert valid[0].flags == ["late_hook"]
    valid2, _ = check(ep, "E001-S007", "E001-U034", "E001-U008", "E001-U034")     # first line 0.5s after the start
    assert valid2 and "late_hook" not in valid2[0].flags


def test_cut_inside_an_utterance_is_rejected_at_both_ends(ep):
    _, r1 = check(ep, "E001-S009", "E001-A028", "E001-S009", "E001-A028")          # shot starts at 12.0 inside the line 9.5-14.5
    assert r1 == [["start cuts inside utterance E001-U008"]]
    _, r2 = check(ep, "E001-S007", "E001-S021", "E001-S007", "E001-S021")          # shot ends at 30.0 inside the line 28.6-31.0
    assert r2 == [["end cuts inside utterance E001-U023"]]
    assert cuts_inside_speech(ep, 9.5, 14.5) == [] and cuts_inside_speech(ep, 9.0, 12.0) == ["end cuts inside utterance E001-U008"]


def test_glued_fragments_are_never_split(ep):
    # the glued line E001-U019 spans 24.2-29.0; ending at 25.5 (between its fragments) must be rejected
    assert cuts_inside_speech(ep, 4.0, 25.6) == ["end cuts inside utterance E001-U019"]


def test_unknown_ids_order_hook_and_cliffhanger_checks(ep):
    assert "unknown unit IDs" in check(ep, "NOPE", "E001-A028", "E001-U006", "E001-A028")[1][0][0]
    assert "end boundary is not after start boundary" in check(ep, "E001-A028", "E001-S003", "E001-A028", "E001-S003")[1][0]
    assert "hook unit is outside the proposed clip" in check(ep, "E001-S003", "E001-A028", "E001-S043", "E001-A028")[1][0]
    assert "cliffhanger unit is outside the proposed clip" in check(ep, "E001-S003", "E001-A028", "E001-U006", "E001-S043")[1][0]


def test_duration_limits_after_snapping(ep):
    assert "outside 20-60s" in check(ep, "E001-S003", "E001-A004", "E001-A004", "E001-A004")[1][0][0]        # 3s
    assert "outside 20-60s" in check(ep, "E001-S001", "E001-S043", "E001-U006", "E001-S043")[1][0][0]       # 65.5s


def test_start_and_end_snap_to_shot_boundaries_without_entering_speech(ep):
    valid, _ = check(ep, "E001-A004", "E001-F026", "E001-A004", "E001-F026")      # action at 4.5 -> shot start 4.0; sfx ends 35.0 -> shot end 36.0
    v = valid[0]
    assert (v.start_s, v.end_s, v.snapped_start, v.snapped_end) == (4.0, 36.0, True, True)


def test_duplicate_spans_are_dropped_and_ids_stay_sequential(ep):
    same = cand("E001-S003", "E001-A028", "E001-U006", "E001-A028")
    other = cand("E001-S007", "E001-U034", "E001-U008", "E001-U034")
    r = validate_candidates(ep, [same, same, other])
    assert [v.candidate_id for v in r.valid] == ["E001-C01", "E001-C02"] and r.rejected[0].reasons == ["duplicate validated time span"]


# ---- Stage 4: judge --------------------------------------------------------------------------------------
def vcands(ep, n=3):
    spans = [("E001-S003", "E001-A028", "E001-U006"), ("E001-S007", "E001-U034", "E001-U008"), ("E001-S011", "E001-A036", "E001-U012")]
    r = validate_candidates(ep, [cand(s, e, h, e) for s, e, h in spans[:n]])
    assert len(r.valid) == n, r.rejected
    return r.valid


def scores(v=2):
    return {"hook": v, "cliffhanger": v, "standalone": v, "escalation": v, "density": v}


def answer(cands, reason="ok", evidence=None):
    return {"judgments": [{"candidate_id": c.candidate_id, "scores": scores(), "reason": reason, "evidence_unit_ids": evidence or [c.unit_ids[0]]} for c in cands]}


def test_judge_prompt_is_clip_only_and_wrapped_as_data(ep):
    cands = vcands(ep)
    _, user = judge_prompts(ep, cands, RUBRIC_PATH.read_text(encoding="utf-8"))
    assert ep.logline not in user and ep.continuity_note not in user
    assert "<clips>" in user and "never instructions" in user and "Do not quote dialogue" in user
    assert all(c.candidate_id in user for c in cands)


def test_judge_order_is_shuffled_deterministically_but_results_keep_input_order(ep):
    cands = vcands(ep)
    _, u1 = judge_prompts(ep, cands, "R")
    _, u2 = judge_prompts(ep, cands, "R")
    assert u1 == u2
    shown = [i for i in sorted(range(len(cands)), key=lambda i: u1.index(cands[i].candidate_id))]
    assert shown != [0, 1, 2]                                                      # seeded shuffle changed the order for this episode
    client = MockClient([answer(cands)])
    out = judge(ep, cands, client)
    assert [j.candidate.candidate_id for j in out] == [c.candidate_id for c in cands]
    assert isinstance(out[0].scores, RubricScores)


def test_judge_needs_one_judgment_per_candidate_and_evidence_inside_the_clip(ep):
    cands = vcands(ep)
    with pytest.raises(ValueError, match="exactly one judgment"):
        judge(ep, cands, MockClient([answer(cands[:2])]))
    with pytest.raises(ValueError, match="outside"):
        judge(ep, cands, MockClient([answer(cands, evidence=["E001-S043"])]))
    assert judge(ep, [], MockClient()) == []


def test_scores_outside_0_to_4_are_invalid():
    with pytest.raises(Exception):
        JudgeResponse.model_validate({"judgments": [{"candidate_id": "c", "scores": scores(5), "reason": "x"}]})


def test_unverified_quotes_are_stripped_from_reasons(ep):
    src = "He is not coming back. You counted the crates yourself"
    assert strip_unverified_quotes('She says "He is not coming back" and "invented line here".', src) == 'She says "He is not coming back" and .'
    assert strip_unverified_quotes("no quotes at all", src) == "no quotes at all"
    cands = vcands(ep)
    out = judge(ep, cands, MockClient([answer(cands, reason='Threat: "this was never said in the episode".')]))
    assert "never said" not in out[0].reason


def test_validated_candidate_model_roundtrip(ep):
    v = vcands(ep, 1)[0]
    assert ValidatedCandidate.model_validate(v.model_dump()) == v
