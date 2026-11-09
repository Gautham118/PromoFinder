"""Stage 3: deterministic candidate validation, safe snapping, and source-quote reconstruction."""
from __future__ import annotations

from ..models import CandidateRejection, ParsedEpisode, ProposedCandidate, ValidationResult, ValidatedCandidate

MIN_DURATION_S = 20.0
MAX_DURATION_S = 60.0
START_SNAP_S = 0.5
END_SNAP_S = 1.0
HOOK_WINDOW_S = 3.0  # D3: measure from the first spoken utterance, flag only.
EPS = 0.005


def _overlaps(start: float, end: float, other_start: float, other_end: float) -> bool:
    return other_start < end - EPS and other_end > start + EPS


def _units_in_span(episode: ParsedEpisode, start: float, end: float) -> list:
    return [u for u in episode.units if _overlaps(start, end, u.start_s, u.end_s)]


def _snap_start(episode: ParsedEpisode, start: float) -> tuple[float, bool]:
    shots = [u for u in episode.units if u.kind == "shot" and EPS <= start - u.start_s <= START_SNAP_S + EPS]
    if not shots:
        return start, False
    shot = max(shots, key=lambda u: u.start_s)
    speech = [u for u in episode.units if u.kind == "utterance" and _overlaps(shot.start_s, start, u.start_s, u.end_s)]
    return (start, False) if speech else (shot.start_s, True)


def _snap_end(episode: ParsedEpisode, end: float) -> tuple[float, bool]:
    shots = [u for u in episode.units if u.kind == "shot" and EPS <= u.end_s - end <= END_SNAP_S + EPS]
    if not shots:
        return end, False
    shot = min(shots, key=lambda u: u.end_s)
    speech = [u for u in episode.units if u.kind == "utterance" and _overlaps(end, shot.end_s, u.start_s, u.end_s)]
    return (end, False) if speech else (shot.end_s, True)


def cuts_inside_speech(episode: ParsedEpisode, start: float, end: float) -> list[str]:
    """CLAUDE.md 6a/7: never cut inside an utterance. A shot boundary is NOT a safe cut when a line spans it."""
    reasons: list[str] = []
    for u in episode.units:
        if u.kind != "utterance":
            continue
        if u.start_s + EPS < start < u.end_s - EPS:
            reasons.append(f"start cuts inside utterance {u.unit_id}")
        if u.start_s + EPS < end < u.end_s - EPS:
            reasons.append(f"end cuts inside utterance {u.unit_id}")
    return reasons


def validate_candidates(episode: ParsedEpisode, candidates: list[ProposedCandidate]) -> ValidationResult:
    """Reject instead of repairing model choices; only documented visual boundary snapping is automatic."""
    by_id = {u.unit_id: u for u in episode.units}
    valid: list[ValidatedCandidate] = []
    rejected: list[CandidateRejection] = []
    seen: set[tuple[float, float]] = set()
    for index, candidate in enumerate(candidates, 1):
        ids = (candidate.start_id, candidate.end_id, candidate.hook_id, candidate.cliffhanger_id)
        unknown = [value for value in ids if value not in by_id]
        if unknown:
            rejected.append(CandidateRejection(candidate_index=index, reasons=[f"unknown unit IDs: {', '.join(sorted(set(unknown)))}"]))
            continue
        start, end = by_id[candidate.start_id].start_s, by_id[candidate.end_id].end_s
        reasons: list[str] = []
        if end <= start + EPS:
            reasons.append("end boundary is not after start boundary")
        hook, cliff = by_id[candidate.hook_id], by_id[candidate.cliffhanger_id]
        if not _overlaps(start, end, hook.start_s, hook.end_s):
            reasons.append("hook unit is outside the proposed clip")
        if not _overlaps(start, end, cliff.start_s, cliff.end_s):
            reasons.append("cliffhanger unit is outside the proposed clip")
        if hook.start_s > cliff.end_s + EPS:
            reasons.append("hook occurs after cliffhanger")
        if reasons:
            rejected.append(CandidateRejection(candidate_index=index, reasons=reasons))
            continue
        snapped_start, did_snap_start = _snap_start(episode, start)
        snapped_end, did_snap_end = _snap_end(episode, end)
        cuts = cuts_inside_speech(episode, snapped_start, snapped_end)
        if cuts:
            rejected.append(CandidateRejection(candidate_index=index, reasons=cuts))
            continue
        duration = round(snapped_end - snapped_start, 2)
        if not MIN_DURATION_S - EPS <= duration <= MAX_DURATION_S + EPS:
            rejected.append(CandidateRejection(candidate_index=index, reasons=[f"duration {duration:.2f}s is outside {MIN_DURATION_S:.0f}-{MAX_DURATION_S:.0f}s after snapping"]))
            continue
        key = (snapped_start, snapped_end)
        if key in seen:
            rejected.append(CandidateRejection(candidate_index=index, reasons=["duplicate validated time span"]))
            continue
        seen.add(key)
        span_units = _units_in_span(episode, snapped_start, snapped_end)
        flags: list[str] = []
        speech = [u for u in span_units if u.kind == "utterance"]
        if not speech:
            flags.append("no_spoken_line")
        elif speech[0].start_s - snapped_start > HOOK_WINDOW_S + EPS:
            flags.append("late_hook")
        valid.append(ValidatedCandidate(
            candidate_id=f"{episode.episode_id}-C{len(valid) + 1:02d}", start_id=candidate.start_id, end_id=candidate.end_id,
            hook_id=candidate.hook_id, cliffhanger_id=candidate.cliffhanger_id, start_s=snapped_start, end_s=snapped_end,
            duration_s=duration, hook_quote=hook.text, cliffhanger_quote=cliff.text,
            unit_ids=[u.unit_id for u in span_units], flags=flags, snapped_start=did_snap_start, snapped_end=did_snap_end,
        ))
    return ValidationResult(valid=valid, rejected=rejected)
