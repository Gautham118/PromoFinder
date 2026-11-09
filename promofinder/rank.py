"""Stage 5a: gate, weighted rank, overlap/cap/recap rules (CLAUDE.md section 7). Pure functions, no LLM."""
from __future__ import annotations

from collections import defaultdict

from .audit import dup_pairs
from .models import JudgedCandidate, ParsedEpisode, RankedEntry, RubricScores, ValidatedCandidate

GATE_MIN = 2
WEIGHTS = {"hook": 0.3, "cliffhanger": 0.3, "standalone": 0.2, "escalation": 0.2}   # UNVALIDATED (D8)
OVERLAP_LIMIT = 0.30          # overlap / shorter clip; a pick overlapping a better pick by more than this is dropped
MAX_PER_EPISODE = 2
GLOBAL_TOP_N = 15


def episode_of(candidate_id: str) -> str:
    return candidate_id.rsplit("-C", 1)[0]


def passes_gate(s: RubricScores) -> bool:
    return min(s.hook, s.cliffhanger, s.standalone) >= GATE_MIN


def weighted_score(s: RubricScores) -> float:
    return round(sum(getattr(s, k) * w for k, w in WEIGHTS.items()), 3)


def overlap_fraction(a: ValidatedCandidate, b: ValidatedCandidate) -> float:
    ov = max(0.0, min(a.end_s, b.end_s) - max(a.start_s, b.start_s))
    return round(ov / min(a.duration_s, b.duration_s), 3)


def recap_flag(prev: ParsedEpisode | None, ep: ParsedEpisode, cand: ValidatedCandidate) -> bool:
    """True if a spoken line in the clip near-duplicates a line of the previous episode (similarity >= 0.8)."""
    if prev is None:
        return False
    ids = set(cand.unit_ids)
    return bool(dup_pairs(prev.units, [u for u in ep.units if u.unit_id in ids]))


def rank_candidates(judged: list[JudgedCandidate], recap: frozenset[str] | set[str] = frozenset(), *,
                    max_per_episode: int = MAX_PER_EPISODE, top_n: int = GLOBAL_TOP_N) -> list[RankedEntry]:
    """Every judged candidate comes back once, with a status. Recap-flagged picks sort after unflagged ones (demoted)."""
    def key(j: JudgedCandidate):
        c = j.candidate
        return (c.candidate_id in recap, -weighted_score(j.scores), -j.scores.density, c.start_s, c.candidate_id)

    def entry(j: JudgedCandidate, status: str, rank: int | None = None) -> RankedEntry:
        return RankedEntry(judged=j, weighted=weighted_score(j.scores), status=status, rank=rank,
                           recap=j.candidate.candidate_id in recap)

    by_ep: dict[str, list[JudgedCandidate]] = defaultdict(list)
    for j in judged:
        by_ep[episode_of(j.candidate.candidate_id)].append(j)
    out: list[RankedEntry] = []
    kept: list[JudgedCandidate] = []
    for ep_id in sorted(by_ep):
        chosen: list[JudgedCandidate] = []
        for j in sorted(by_ep[ep_id], key=key):
            if not passes_gate(j.scores):
                out.append(entry(j, "below_gate"))
            elif any(overlap_fraction(j.candidate, k.candidate) > OVERLAP_LIMIT for k in chosen):
                out.append(entry(j, "dropped_overlap"))
            elif len(chosen) >= max_per_episode:
                out.append(entry(j, "dropped_cap"))
            else:
                chosen.append(j)
        kept.extend(chosen)
    kept.sort(key=key)
    ranked = [entry(j, "ranked", i) if i <= top_n else entry(j, "not_in_top_n") for i, j in enumerate(kept, 1)]
    return ranked + out
