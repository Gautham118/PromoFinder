"""Stage 5b: run Stages 2-4 per episode. Every failure is logged per episode; nothing crashes the run."""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from typing import Callable

from .config import RUBRIC_PATH
from .llm import LLMClient
from .llm.client import LLMError
from .models import EpisodeResult, Failure, ParsedEpisode
from .rank import recap_flag
from .stages import judge as judge_mod
from .stages import propose as propose_mod
from .stages.judge import judge
from .stages.propose import propose
from .stages.validate import validate_candidates


def compute_prompt_hash(rubric_path: str | Path = RUBRIC_PATH) -> str:
    """One hash over the rubric and both prompt templates, so any prompt edit changes it (D9 freeze check)."""
    h = hashlib.sha256()
    for part in (Path(rubric_path).read_text(encoding="utf-8"), inspect.getsource(propose_mod.proposer_prompts),
                 inspect.getsource(judge_mod.judge_prompts)):
        h.update(part.encode("utf-8"))
    return h.hexdigest()[:12]


def _fail(ep_id: str, stage: str, kind: str, e: Exception | str) -> Failure:
    return Failure(episode_id=ep_id, stage=stage, kind=kind, detail=str(e)[:500])


def process_episode(ep: ParsedEpisode, client: LLMClient, *, cache_bypass: bool = False) -> EpisodeResult:
    res = EpisodeResult(episode_id=ep.episode_id)
    try:
        res.proposal = propose(ep, client, cache_bypass=cache_bypass)
    except (LLMError, ValueError) as e:
        res.failures.append(_fail(ep.episode_id, "propose", "llm_error", e))
        return res
    res.validation = validate_candidates(ep, res.proposal.candidates)
    for r in res.validation.rejected:
        res.failures.append(_fail(ep.episode_id, "validate", "rejected_candidate", f"candidate {r.candidate_index}: " + "; ".join(r.reasons)))
    if not res.validation.valid:
        return res
    try:
        res.judged = judge(ep, res.validation.valid, client, cache_bypass=cache_bypass)
    except (LLMError, ValueError) as e:       # wrong judgment count, evidence outside the clip, invalid JSON after repair
        res.failures.append(_fail(ep.episode_id, "judge", "judge_error", e))
    return res


def run_episodes(episodes: list[ParsedEpisode], client: LLMClient, *, cache_bypass: bool = False,
                 log: Callable[[str], None] = print) -> list[EpisodeResult]:
    results: list[EpisodeResult] = []
    for i, ep in enumerate(episodes, 1):
        try:
            res = process_episode(ep, client, cache_bypass=cache_bypass)
        except Exception as e:                # last-resort guard: log it loudly, keep the other episodes going
            res = EpisodeResult(episode_id=ep.episode_id, failures=[_fail(ep.episode_id, "pipeline", "unexpected_error", f"{type(e).__name__}: {e}")])
        n_prop = len(res.proposal.candidates) if res.proposal else 0
        n_valid = len(res.validation.valid) if res.validation else 0
        bad = [f for f in res.failures if f.stage != "validate"]
        log(f"[{i}/{len(episodes)}] {ep.episode_id}: proposed {n_prop}, valid {n_valid}, judged {len(res.judged)}"
            + (f"  !! {bad[0].stage} {bad[0].kind}: {bad[0].detail[:120]}" if bad else ""))
        results.append(res)
    return results


def recap_set(episodes: list[ParsedEpisode], results: list[EpisodeResult]) -> set[str]:
    """Candidate ids whose spoken lines near-duplicate the previous episode (needs that episode in the same run)."""
    by_num = {e.episode_number: e for e in episodes if e.episode_number is not None}
    by_id = {e.episode_id: e for e in episodes}
    out: set[str] = set()
    for r in results:
        ep = by_id[r.episode_id]
        prev = by_num.get((ep.episode_number or 0) - 1)
        for j in r.judged:
            if recap_flag(prev, ep, j.candidate):
                out.add(j.candidate.candidate_id)
    return out
