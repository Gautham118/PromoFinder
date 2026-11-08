"""Stage 1 audit over all episode files: metrics, report text, CSVs, CLAUDE.md claim checks.

All metric functions are pure (parsed episode in, numbers out) so they can be tested on the
synthetic sample. Run:  python -m promofinder audit --input data/pack --out outputs/audit
"""
from __future__ import annotations

import argparse
import csv
import difflib
import math
import re
import statistics as st
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

from .models import Block, ParsedEpisode, Unit
from .parser import ParseError, list_episode_files, norm_token, parse_text
from .timeline import boundaries_inside, shot_boundaries
from .units import ends_with_ellipsis, speaker_key, starts_with_ellipsis, unwrap_quotes

ENDING_WINDOW_S = 10.0        # "at the end" for cliffhanger-word location
TAIL_S = 5.0                  # window for ending signals
DUP_MIN_WORDS = 4
DUP_THRESHOLD = 0.8           # CLAUDE.md section 7
RECAP_THRESHOLD = 0.6         # looser "reworded" threshold, reported separately
RECAP_WINDOW = 5              # last N utterances of ep N-1 vs first N of ep N
CONTROL_OFFSET = 10           # control pairs: episode N vs N+10 (not adjacent)

RE_CLIFF = re.compile(r"cliff\s*-?\s*hang", re.I)
RE_BLACK = re.compile(r"\b(?:cut|fade|smash\s+cut|dissolve)s?\s+(?:to\s+|out\s+to\s+)?black\b|\bfade[sd]?\s+out\b|\bblack\s*out\b|\bblack\s+screen\b", re.I)
RE_PREV_REF = re.compile(r"\b(?:previous|last|prior)\s+episode\b|\bpicks?\s+up\b|\bcontinues?\s+from\b|\bends?\s+with\b|\bended\b|\bcliffhang", re.I)


# ---------------------------------------------------------------- per-episode pure metrics
def _num(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(xs), "min": min(xs), "median": round(st.median(xs), 2), "max": max(xs)}


def mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


def cliffhanger_mentions(ep: ParsedEpisode) -> list[dict]:
    out = []
    for b in ep.blocks:
        if RE_CLIFF.search(b.text) or RE_CLIFF.search(b.label_raw):
            out.append({"episode_id": ep.episode_id, "where": b.kind, "block_id": b.block_id, "start_s": b.start_s,
                        "end_s": b.end_s, "offset_from_end_s": round(ep.duration_s - b.end_s, 2),
                        "in_ending": ep.duration_s - b.end_s <= ENDING_WINDOW_S, "text": b.text})
    for where, val in (("logline", ep.logline), ("continuity_note", ep.continuity_note)):
        if val and RE_CLIFF.search(val):
            out.append({"episode_id": ep.episode_id, "where": where, "block_id": "", "start_s": "", "end_s": "",
                        "offset_from_end_s": "", "in_ending": "", "text": val})
    return out


def first_times(ep: ParsedEpisode) -> dict:
    spoken = [b.start_s for b in ep.blocks if b.kind == "dialogue"]
    noncam = [b.start_s for b in ep.blocks if b.kind != "camera"]
    return {"first_spoken_s": min(spoken) if spoken else None, "first_noncamera_s": min(noncam) if noncam else None}


def ending_signals(ep: ParsedEpisode) -> dict:
    """Code-checkable ending signals only. This is NOT a story judgment."""
    d = ep.duration_s
    last = max(ep.blocks, key=lambda b: (b.end_s, b.start_s, b.block_id))
    noncam = [b for b in ep.blocks if b.kind != "camera"]
    last_nc = max(noncam, key=lambda b: (b.end_s, b.start_s, b.block_id)) if noncam else None
    tail = [b for b in ep.blocks if b.end_s > d - TAIL_S]
    tail10 = [b for b in ep.blocks if b.end_s > d - ENDING_WINDOW_S]
    cliff_word = any(b.kind != "dialogue" and RE_CLIFF.search(b.text) for b in tail10)
    black = any(b.kind != "dialogue" and RE_BLACK.search(b.text) for b in tail)
    utts = [u for u in ep.units if u.kind == "utterance"]
    last_utt = max(utts, key=lambda u: (u.end_s, u.start_s)) if utts else None
    utt_in_tail = last_utt is not None and last_utt.end_s > d - TAIL_S
    q = bool(utt_in_tail and last_utt.text.rstrip().endswith("?"))
    ell = bool(utt_in_tail and ends_with_ellipsis(last_utt.text))
    last_nc_kind = last_nc.kind if last_nc else None
    if cliff_word:
        etype = "self_labeled_cliffhanger"
    elif q:
        etype = "ends_on_question"
    elif black:
        etype = "cut_to_black"
    elif last_nc_kind == "dialogue":
        etype = "ends_on_dialogue"
    else:
        etype = "ends_on_action_or_sfx"
    return {"last_block_kind": last.kind, "last_noncamera_kind": last_nc_kind,
            "last_noncamera_end_gap_s": round(d - last_nc.end_s, 2) if last_nc else None,
            "end_cut_to_black": black, "end_cliffhanger_word": cliff_word, "end_last_line_question": q,
            "end_last_line_ellipsis": ell, "ending_type": etype}


def dialogue_crossings(ep: ParsedEpisode) -> dict:
    bnd = shot_boundaries(ep.blocks)
    lines = [b for b in ep.blocks if b.kind == "dialogue"]
    crossing = [(b, boundaries_inside(b.start_s, b.end_s, bnd)) for b in lines]
    crossing = [(b, x) for b, x in crossing if x]
    utts = [u for u in ep.units if u.kind == "utterance"]
    ucross = [u for u in utts if boundaries_inside(u.start_s, u.end_s, bnd)]
    return {"dialogue_lines": len(lines), "lines_crossing_shot": len(crossing),
            "crossing_line_durations_s": [round(b.end_s - b.start_s, 2) for b, _ in crossing],
            "max_boundaries_in_one_line": max((len(x) for _, x in crossing), default=0),
            "utterance_groups": len(utts), "groups_crossing_shot": len(ucross)}


def glue_stats(ep: ParsedEpisode) -> dict:
    pairs = ep.audit.glue_pairs
    a_ids = {p.a_id for p in pairs}
    b_ids = {p.b_id for p in pairs}
    lines = [b for b in ep.blocks if b.kind == "dialogue"]
    ends = [b for b in lines if ends_with_ellipsis(b.text)]
    starts = [b for b in lines if starts_with_ellipsis(b.text)]
    sizes = [len(u.source_line_ids) for u in ep.units if u.kind == "utterance"]
    return {"glued": sum(p.accepted for p in pairs), "rejected": sum(not p.accepted for p in pairs),
            "rejected_reasons": dict(Counter(p.reason for p in pairs if not p.accepted)),
            "chains_3plus": sum(1 for s in sizes if s >= 3), "max_chain": max(sizes, default=0),
            "lines_ending_ellipsis": len(ends), "lines_starting_ellipsis": len(starts),
            "trailing_off_unpaired": sum(1 for b in ends if b.block_id not in a_ids),
            "orphan_starts_unpaired": sum(1 for b in starts if b.block_id not in b_ids),
            "dialogue_without_quotes": sum(1 for b in lines if unwrap_quotes(b.text) == b.text.strip())}


def estimate_tokens(raw_text: str, ep: ParsedEpisode) -> dict:
    """ESTIMATES (no tokenizer available offline). Method stated in the report."""
    compact = sum(len(b.text) + len(b.speaker_label or b.kind) + 12 for b in ep.blocks)
    compact += len(ep.logline or "") + len(ep.continuity_note or "")
    return {"est_tokens_file_chars4": math.ceil(len(raw_text) / 4),
            "est_tokens_file_words": math.ceil(len(raw_text.split()) * 1.33),
            "est_tokens_compact_chars4": math.ceil(compact / 4)}


def normalize_for_dup(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def dup_pairs(prev: list[Unit], cur: list[Unit], threshold: float = DUP_THRESHOLD) -> list[dict]:
    """Utterance pairs (>= DUP_MIN_WORDS words each) with similarity >= threshold."""
    out = []
    pn = [(u, normalize_for_dup(u.text)) for u in prev if u.kind == "utterance"]
    cn = [(u, normalize_for_dup(u.text)) for u in cur if u.kind == "utterance"]
    for u, a in cn:
        if len(a.split()) < DUP_MIN_WORDS:
            continue
        for v, b in pn:
            if len(b.split()) < DUP_MIN_WORDS:
                continue
            if a == b:
                r = 1.0
            else:
                sm = difflib.SequenceMatcher(None, a, b)
                if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                    continue
                r = sm.ratio()
            if r >= threshold:
                out.append({"cur_unit": u.unit_id, "prev_unit": v.unit_id, "ratio": round(r, 3), "exact": a == b,
                            "cur_text": u.text, "prev_text": v.text})
    return out


def recap_reopen(prev: ParsedEpisode, cur: ParsedEpisode, threshold: float = RECAP_THRESHOLD) -> Optional[float]:
    """Best similarity between the last RECAP_WINDOW utterances of prev and the first of cur."""
    pu = [u for u in prev.units if u.kind == "utterance"][-RECAP_WINDOW:]
    cu = [u for u in cur.units if u.kind == "utterance"][:RECAP_WINDOW]
    best = 0.0
    for a in cu:
        for b in pu:
            x, y = normalize_for_dup(a.text), normalize_for_dup(b.text)
            if len(x.split()) >= DUP_MIN_WORDS and len(y.split()) >= DUP_MIN_WORDS:
                best = max(best, difflib.SequenceMatcher(None, x, y).ratio())
    return round(best, 3) if best >= threshold else None


def repeated_lines(episodes: list[ParsedEpisode]) -> dict:
    """Exact (normalised) utterance texts that occur in 2+ different episodes, any length."""
    where: dict[str, set[str]] = {}
    for ep in episodes:
        for u in ep.units:
            if u.kind == "utterance":
                where.setdefault(normalize_for_dup(u.text), set()).add(ep.episode_id)
    multi = [k for k, v in where.items() if len(v) > 1]
    return {"distinct": len(where), "repeated": len(multi),
            "repeated_min_words": sum(1 for k in multi if len(k.split()) >= DUP_MIN_WORDS),
            "repeated_by_words": dict(sorted(Counter(min(len(k.split()), 6) for k in multi).items()))}


def label_stats(episodes: list[ParsedEpisode]) -> dict:
    forms = Counter()
    mod_tokens, tone_tokens, nondlg = Counter(), Counter(), Counter()
    speakers = Counter()
    for ep in episodes:
        for b in ep.blocks:
            if b.kind != "dialogue":
                nondlg[b.label_raw.upper()] += 1
                continue
            speakers[speaker_key(b.speaker_label)] += 1
            forms["tone_only" if b.tone and not b.modifiers else "modifier_only" if b.modifiers and not b.tone
                  else "both" if b.tone and b.modifiers else "none"] += 1
            mod_tokens.update(b.modifiers)
            tone_tokens.update(b.tone)
    names = list(speakers)
    contained = sum(1 for a in names for b in names if a != b and re.search(rf"\b{re.escape(a)}\b", b))
    return {"forms": dict(forms), "modifier_tokens": mod_tokens, "tone_tokens": tone_tokens, "non_dialogue_labels": nondlg,
            "distinct_speaker_labels": len(names), "labels_contained_in_other_labels": contained, "speakers": speakers}


def scene_checks(ep: ParsedEpisode) -> dict:
    sc = ep.scenes
    resets = sum(1 for a, b in zip(sc, sc[1:]) if a.end_s is not None and b.start_s is not None and b.start_s < a.end_s - 0.005)
    later_zero = sum(1 for s in sc[1:] if s.start_s == 0)
    outside = sum(1 for b in ep.blocks if sc[b.scene_index - 1].start_s is not None and sc[b.scene_index - 1].end_s is not None
                  and (b.start_s < sc[b.scene_index - 1].start_s - 0.005 or b.end_s > sc[b.scene_index - 1].end_s + 0.005))
    return {"scene_count": len(sc), "scene_resets": resets, "later_scenes_starting_at_zero": later_zero,
            "blocks_outside_their_scene": outside}


def episode_row(ep: ParsedEpisode, raw_text: str) -> dict:
    a = ep.audit
    gaps, ovl, dov = a.camera_gaps, a.camera_overlaps, a.dialogue_overlaps
    ft, es, dc, gs, sc = first_times(ep), ending_signals(ep), dialogue_crossings(ep), glue_stats(ep), scene_checks(ep)
    cm = cliffhanger_mentions(ep)
    script_cm = [m for m in cm if m["where"] in ("camera", "action", "sfx", "dialogue")]
    row = {
        "episode_id": ep.episode_id, "file": ep.source_name, "duration_s": ep.duration_s, "duration_mmss": mmss(ep.duration_s),
        "last_block_end_s": ep.last_block_end_s, "scenes": sc["scene_count"], "blocks": len(ep.blocks),
        "camera": sum(b.kind == "camera" for b in ep.blocks), "action": sum(b.kind == "action" for b in ep.blocks),
        "sfx": sum(b.kind == "sfx" for b in ep.blocks), "dialogue_lines": dc["dialogue_lines"],
        "utterance_groups": dc["utterance_groups"], "units": len(ep.units),
        "camera_gaps": len(gaps), "camera_gap_largest_s": max((g.size_s for g in gaps), default=0.0),
        "camera_gap_total_s": round(sum(g.size_s for g in gaps), 2), "camera_gaps_with_activity": sum(g.activity_during for g in gaps),
        "camera_overlaps": len(ovl), "camera_overlap_largest_s": max((o.size_s for o in ovl), default=0.0),
        "camera_first_start_s": a.camera_first_start_s, "camera_last_end_s": a.camera_last_end_s,
        "dialogue_overlaps": len(dov), "dialogue_overlap_largest_s": max((o.size_s for o in dov), default=0.0),
        "lines_crossing_shot": dc["lines_crossing_shot"], "groups_crossing_shot": dc["groups_crossing_shot"],
        "max_boundaries_in_one_line": dc["max_boundaries_in_one_line"],
        "glued_pairs": gs["glued"], "rejected_ellipsis_pairs": gs["rejected"], "chains_3plus": gs["chains_3plus"],
        "max_chain": gs["max_chain"], "lines_ending_ellipsis": gs["lines_ending_ellipsis"],
        "lines_starting_ellipsis": gs["lines_starting_ellipsis"], "trailing_off_unpaired": gs["trailing_off_unpaired"],
        "orphan_starts_unpaired": gs["orphan_starts_unpaired"], "dialogue_without_quotes": gs["dialogue_without_quotes"],
        "cliffhanger_in_script": len(script_cm), "cliffhanger_in_script_ending": sum(bool(m["in_ending"]) for m in script_cm),
        "cliffhanger_in_logline": int(any(m["where"] == "logline" for m in cm)),
        "cliffhanger_in_continuity": int(any(m["where"] == "continuity_note" for m in cm)),
        "first_spoken_s": ft["first_spoken_s"], "first_noncamera_s": ft["first_noncamera_s"],
        "last_block_kind": es["last_block_kind"], "last_noncamera_kind": es["last_noncamera_kind"],
        "last_noncamera_end_gap_s": es["last_noncamera_end_gap_s"], "end_cut_to_black": es["end_cut_to_black"],
        "end_cliffhanger_word": es["end_cliffhanger_word"], "end_last_line_question": es["end_last_line_question"],
        "end_last_line_ellipsis": es["end_last_line_ellipsis"], "ending_type": es["ending_type"],
        "unparsed_lines": len(a.unparsed_lines), "warnings": len(a.warnings), "nonstandard_times": len(a.nonstandard_time_strings),
        "blocks_out_of_start_order": a.blocks_out_of_start_order, "scene_resets": sc["scene_resets"],
        "later_scenes_at_zero": sc["later_scenes_starting_at_zero"], "blocks_outside_scene": sc["blocks_outside_their_scene"],
        "has_logline": ep.format_variant.get("has_logline"), "has_continuity": ep.format_variant.get("has_continuity"),
    }
    row.update(estimate_tokens(raw_text, ep))
    return row


# ---------------------------------------------------------------- corpus level
def format_variants(episodes: list[ParsedEpisode]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for ep in episodes:
        key = ", ".join(f"{k}={v}" for k, v in sorted(ep.format_variant.items()))
        groups.setdefault(key, []).append(ep.episode_id)
    return groups


def adjacent_duplicates(episodes: list[ParsedEpisode]) -> tuple[list[dict], dict[str, dict]]:
    """Per episode: duplicates against the previous episode, plus a non-adjacent control."""
    by_num = {e.episode_number: e for e in episodes if e.episode_number is not None}
    rows: list[dict] = []
    per_ep: dict[str, dict] = {}
    for ep in episodes:
        n = ep.episode_number
        prev, ctrl = by_num.get((n or 0) - 1), by_num.get((n or 0) - CONTROL_OFFSET)
        pairs = dup_pairs(prev.units, ep.units) if prev else []
        cpairs = dup_pairs(ctrl.units, ep.units) if ctrl else []
        for p in pairs:
            rows.append({"episode_id": ep.episode_id, "prev_episode_id": prev.episode_id, **p})
        per_ep[ep.episode_id] = {
            "has_prev": prev is not None, "dup_exact": sum(p["exact"] for p in pairs), "dup_near": sum(not p["exact"] for p in pairs),
            "has_ctrl": ctrl is not None, "ctrl_exact": sum(p["exact"] for p in cpairs), "ctrl_near": sum(not p["exact"] for p in cpairs),
            "recap_best": recap_reopen(prev, ep) if prev else None}
    return rows, per_ep


def claim_checks(rows: list[dict], episodes: list[ParsedEpisode], labels: dict, dups: dict[str, dict], failures: int) -> list[tuple[str, str, str]]:
    """(claim, status, evidence). Statuses: HELD, PARTLY, DID NOT HOLD, UNVERIFIED (needs story judgment)."""
    R = lambda k: [r[k] for r in rows]
    n = len(rows)
    out: list[tuple[str, str, str]] = []
    dur = R("duration_s")
    out.append(("All files share one header/script structure (EPISODE NUMBER, LOGLINE, CONTINUITY NOTES, SCRIPT START/END)",
                "HELD" if failures == 0 and all(r["has_logline"] == "yes" and r["has_continuity"] == "yes" for r in rows) else "PARTLY",
                f"{n - failures}/{n} parsed; logline present {sum(r['has_logline'] == 'yes' for r in rows)}/{n}; continuity notes present {sum(r['has_continuity'] == 'yes' for r in rows)}/{n}"))
    sc = R("scenes")
    out.append(("1 to 3 scenes per episode", "HELD" if min(sc) >= 1 and max(sc) <= 3 else "DID NOT HOLD", f"scene counts {dict(sorted(Counter(sc).items()))}"))
    out.append(("Scene times are episode-global (not reset per scene)", "HELD" if sum(R("scene_resets")) == 0 and sum(R("later_scenes_at_zero")) == 0 else "DID NOT HOLD",
                f"resets={sum(R('scene_resets'))}, later scenes starting at 0={sum(R('later_scenes_at_zero'))}, blocks outside their scene range={sum(R('blocks_outside_scene'))}"))
    out.append(("Timestamps are MM:SS.ss", "HELD" if sum(R("nonstandard_times")) == 0 else "DID NOT HOLD", f"non-standard time strings: {sum(R('nonstandard_times'))}"))
    out.append(("Episode lengths 1:50 to 2:31 (observed in first 6)", "DID NOT HOLD" if min(dur) < 110 or max(dur) > 151 else "HELD",
                f"all 50: {mmss(min(dur))} to {mmss(max(dur))}, median {mmss(st.median(dur))}"))
    fv = format_variants(episodes)
    out.append(("Header/section case varies between episodes", "HELD" if len(fv) > 1 else "DID NOT HOLD",
                f"{len(fv)} distinct format fingerprints; sizes {sorted((len(v) for v in fv.values()), reverse=True)}"))
    f = labels["forms"]
    out.append(("Speaker-label parentheses: tone only / modifier only / both / none all occur", "HELD" if all(f.get(k, 0) > 0 for k in ("tone_only", "modifier_only", "both")) else "PARTLY", f"{f}"))
    out.append(("Speaker labels are unstable (same character, different labels)", "UNVERIFIED",
                f"code cannot resolve identity. Proxy only: {labels['distinct_speaker_labels']} distinct speaker names; {labels['labels_contained_in_other_labels']} (label, label) pairs where one name is a whole-word part of another"))
    out.append(("CAMERA blocks have gaps", "HELD" if sum(R("camera_gaps")) > 0 else "DID NOT HOLD",
                f"{sum(R('camera_gaps'))} gaps in {sum(1 for x in R('camera_gaps') if x)}/{n} episodes; largest {max(R('camera_gap_largest_s'))}s; with other activity during the gap: {sum(R('camera_gaps_with_activity'))}"))
    out.append(("No camera-to-camera overlaps", "HELD" if sum(R("camera_overlaps")) == 0 else "DID NOT HOLD", f"camera overlaps: {sum(R('camera_overlaps'))} (largest {max(R('camera_overlap_largest_s'))}s)"))
    out.append(("Dialogue crosses shot boundaries", "HELD" if sum(R("lines_crossing_shot")) > 0 else "DID NOT HOLD",
                f"{sum(R('lines_crossing_shot'))}/{sum(R('dialogue_lines'))} dialogue lines cross at least one shot boundary"))
    out.append(("Dialogue sentences are split across lines with ellipses; atomic unit is the utterance", "HELD" if sum(R("glued_pairs")) > 0 else "DID NOT HOLD",
                f"{sum(R('glued_pairs'))} glued pairs, {sum(R('rejected_ellipsis_pairs'))} rejected ellipsis pairs, chains of 3+: {sum(R('chains_3plus'))} (max chain {max(R('max_chain'))})"))
    cl = sum(R("cliffhanger_in_script"))
    out.append(("Some SOUND FX/ACTION text names the ending style (mentions 'cliffhanger')", "HELD" if cl > 0 else "DID NOT HOLD",
                f"{cl} script mentions in {sum(1 for x in R('cliffhanger_in_script') if x)}/{n} episodes; {sum(R('cliffhanger_in_script_ending'))} within last {ENDING_WINDOW_S:.0f}s; logline mentions {sum(R('cliffhanger_in_logline'))}; continuity-note mentions {sum(R('cliffhanger_in_continuity'))}"))
    out.append(("Continuity notes of the next episode describe the previous one's ending", "UNVERIFIED", "needs reading comprehension; the audit only counts the word 'cliffhanger' in notes (see line above). Check by hand on 3 pairs"))
    out.append(("Loglines summarize the whole episode including its outcome", "UNVERIFIED", "needs reading comprehension; no code check"))
    ex = sum(d["dup_exact"] for d in dups.values() if d["has_prev"])
    nr = sum(d["dup_near"] for d in dups.values() if d["has_prev"])
    cex = sum(d["ctrl_exact"] for d in dups.values() if d["has_ctrl"])
    cnr = sum(d["ctrl_near"] for d in dups.values() if d["has_ctrl"])
    npair, cpair = sum(d["has_prev"] for d in dups.values()), sum(d["has_ctrl"] for d in dups.values())
    rep = repeated_lines(episodes)
    out.append(("Dialogue is templated: identical/near-identical lines recur across episodes", "PARTLY" if rep["repeated"] > 0 else "DID NOT HOLD",
                f"{rep['repeated']}/{rep['distinct']} distinct lines occur in 2+ episodes, but only {rep['repeated_min_words']} have >= {DUP_MIN_WORDS} words (by word count, 6 = 6+: {rep['repeated_by_words']}), so most repeats are short stock lines. "
                f"Adjacent pairs ({npair}): {ex} exact + {nr} near (>= {DUP_THRESHOLD}); control pairs N vs N-{CONTROL_OFFSET} ({cpair}): {cex} exact + {cnr} near"))
    rc = [d["recap_best"] for d in dups.values() if d["recap_best"] is not None]
    out.append(("Some episodes reopen with a reworded version of the previous episode's last beat", "HELD" if rc else "DID NOT HOLD",
                f"{len(rc)}/{npair} adjacent pairs have a first-{RECAP_WINDOW} vs last-{RECAP_WINDOW} utterance similarity >= {RECAP_THRESHOLD}"))
    et = Counter(R("ending_type"))
    out.append(("Only a minority of episodes end on a true cliffhanger", "UNVERIFIED", f"'true cliffhanger' is a story judgment. Code-checkable signals only: {dict(et)}"))
    fs = [r["first_spoken_s"] for r in rows if r["first_spoken_s"] is not None]
    late = sum(1 for x in fs if x > 3.0)
    out.append(("Some episodes open with several seconds of silent setup before the first spoken line", "HELD" if late > 0 else "DID NOT HOLD",
                f"first spoken line after 3.0s in {late}/{len(fs)} episodes; range {min(fs)}s to {max(fs)}s, median {st.median(fs)}s"))
    out.append(("A 60s promo is about 40-55% of an episode; at most 2-3 non-overlapping promos fit", "HELD" if max(math.floor(d / 40) for d in dur) <= 3 else "DID NOT HOLD",
                f"60s is {60 / max(dur):.0%} to {60 / min(dur):.0%} of an episode; non-overlapping 40s clips per episode: {min(math.floor(d / 40) for d in dur)} to {max(math.floor(d / 40) for d in dur)} (arithmetic only)"))
    return out


# ---------------------------------------------------------------- output
def _fmt_summary(name: str, xs: list[float]) -> str:
    s = _num([x for x in xs if x is not None])
    return f"{name}: min {s['min']}, median {s['median']}, max {s['max']}"


def render_report(rows, episodes, failed, labels, dups, dup_rows, claims, cliff_rows) -> str:
    L: list[str] = []
    P = L.append
    n = len(rows)
    P("=" * 78)
    P("PROMOFINDER STAGE 1 AUDIT")
    P("=" * 78)
    P(f"files found: {n + len(failed)} | parsed: {n} | failed: {len(failed)}")
    for name, err in failed:
        P(f"  !! FAILED TO PARSE: {name}: {err}")
    bad = [(r["episode_id"], r["unparsed_lines"], r["warnings"]) for r in rows if r["unparsed_lines"] or r["warnings"]]
    P(f"episodes with unparsed lines or warnings: {len(bad)}")
    for ep in episodes:
        for u in ep.audit.unparsed_lines:
            P(f"  !! {ep.episode_id} line {u.line_no}: {u.reason}: {u.text}")
        for w in ep.audit.warnings:
            P(f"  !! {ep.episode_id}: {w}")

    P("\n--- 1. FORMAT VARIANTS ---")
    fv = format_variants(episodes)
    for key, ids in sorted(fv.items(), key=lambda kv: -len(kv[1])):
        P(f"{len(ids):3d} files | {key}")
        if len(ids) < n / 2:
            P(f"      files: {', '.join(ids)}")
    nums = sorted(e.episode_number for e in episodes if e.episode_number is not None)
    P(f"episode numbers: {nums[0]}..{nums[-1]}, contiguous={nums == list(range(nums[0], nums[-1] + 1))}")

    P("\n--- 2. LENGTHS AND SCENES ---")
    P(_fmt_summary("duration_s (declared scene end)", [r["duration_s"] for r in rows]))
    P("shortest: " + ", ".join(f"{r['episode_id']} {r['duration_mmss']}" for r in sorted(rows, key=lambda r: r["duration_s"])[:3]))
    P("longest:  " + ", ".join(f"{r['episode_id']} {r['duration_mmss']}" for r in sorted(rows, key=lambda r: -r["duration_s"])[:3]))
    d_diff = [r for r in rows if abs(r["duration_s"] - r["last_block_end_s"]) > 0.005]
    P(f"episodes where declared duration != last block end: {len(d_diff)} " + ", ".join(f"{r['episode_id']} ({r['duration_s']} vs {r['last_block_end_s']})" for r in d_diff[:10]))
    P(f"scene counts: {dict(sorted(Counter(r['scenes'] for r in rows).items()))}")
    P(f"blocks per episode: " + _fmt_summary("", [r["blocks"] for r in rows]).lstrip(": "))
    P(f"non-dialogue labels seen: {dict(labels['non_dialogue_labels'])}")
    P(f"speaker-label forms: {labels['forms']}; distinct speaker names: {labels['distinct_speaker_labels']}")
    P(f"modifier tokens seen: {dict(labels['modifier_tokens'])}")
    P(f"tone vocabulary size: {len(labels['tone_tokens'])}; top 10: {labels['tone_tokens'].most_common(10)}")

    P("\n--- 3. CAMERA GAPS / OVERLAPS, DIALOGUE OVERLAPS ---")
    P(f"camera gaps: {sum(r['camera_gaps'] for r in rows)} in {sum(1 for r in rows if r['camera_gaps'])}/{n} episodes; largest {max(r['camera_gap_largest_s'] for r in rows)}s; "
      f"total gap time {round(sum(r['camera_gap_total_s'] for r in rows), 1)}s; gaps with other blocks active during them: {sum(r['camera_gaps_with_activity'] for r in rows)}")
    allgaps = sorted((g.size_s for ep in episodes for g in ep.audit.camera_gaps), reverse=True)
    P(f"gap size distribution: <=0.5s {sum(g <= 0.5 for g in allgaps)}, 0.5-2s {sum(0.5 < g <= 2 for g in allgaps)}, >2s {sum(g > 2 for g in allgaps)}")
    top = sorted(((g.size_s, ep.episode_id, g.start_s, g.end_s) for ep in episodes for g in ep.audit.camera_gaps), reverse=True)[:5]
    P("largest gaps: " + "; ".join(f"{e} {a}-{b} ({sz}s)" for sz, e, a, b in top))
    ovs = [(ep.episode_id, o.start_s, o.end_s, o.size_s) for ep in episodes for o in ep.audit.camera_overlaps]
    P("camera overlaps (all): " + "; ".join(f"{e} {a}-{b} ({sz}s)" for e, a, b, sz in ovs))
    P(f"first camera block starts after scene start (leading gap): {sum(1 for r in rows if (r['camera_first_start_s'] or 0) > 0.005)} episodes; "
      f"last camera ends before episode end: {sum(1 for r in rows if r['camera_last_end_s'] is not None and r['camera_last_end_s'] < r['duration_s'] - 0.005)} episodes")
    P(f"camera overlaps: {sum(r['camera_overlaps'] for r in rows)} (largest {max(r['camera_overlap_largest_s'] for r in rows)}s)")
    P(f"dialogue-dialogue overlaps: {sum(r['dialogue_overlaps'] for r in rows)} in {sum(1 for r in rows if r['dialogue_overlaps'])}/{n} episodes (largest {max(r['dialogue_overlap_largest_s'] for r in rows)}s)")
    P(f"blocks listed out of start-time order in the file: {sum(r['blocks_out_of_start_order'] for r in rows)}")

    P("\n--- 4. DIALOGUE LINES CROSSING SHOT BOUNDARIES ---")
    cd = [d for ep in episodes for d in dialogue_crossings(ep)["crossing_line_durations_s"]]
    tot = sum(r["dialogue_lines"] for r in rows)
    P(f"{sum(r['lines_crossing_shot'] for r in rows)}/{tot} lines ({sum(r['lines_crossing_shot'] for r in rows) / tot:.0%}) cross >=1 shot boundary; "
      f"utterance groups: {sum(r['groups_crossing_shot'] for r in rows)}/{sum(r['utterance_groups'] for r in rows)}; max boundaries inside one line: {max(r['max_boundaries_in_one_line'] for r in rows)}")
    P(f"durations of crossing lines: {_fmt_summary('s', cd)}")

    P("\n--- 5. ELLIPSIS PAIRS (glue rule: line ends '...', NEXT dialogue line starts '...', same speaker name, gap <= 1.5s) ---")
    allp = [(ep, p) for ep in episodes for p in ep.audit.glue_pairs]
    acc = [(ep, p) for ep, p in allp if p.accepted]
    rej = [(ep, p) for ep, p in allp if not p.accepted]
    P(f"accepted (glued): {len(acc)} | rejected: {len(rej)} {dict(Counter(p.reason for _, p in rej))}")
    P(f"chains of 3+ lines: {sum(r['chains_3plus'] for r in rows)} (max chain length {max(r['max_chain'] for r in rows)})")
    P(f"lines ending in '...': {sum(r['lines_ending_ellipsis'] for r in rows)}; of which with NO continuation pair (trailing off): {sum(r['trailing_off_unpaired'] for r in rows)}")
    P(f"lines starting with '...': {sum(r['lines_starting_ellipsis'] for r in rows)}; of which orphan (no preceding pair): {sum(r['orphan_starts_unpaired'] for r in rows)}")
    P(f"dialogue lines without wrapping quote marks: {sum(r['dialogue_without_quotes'] for r in rows)}")
    chains = [(ep, u) for ep in episodes for u in ep.units if u.kind == "utterance" and len(u.source_line_ids) >= 3]
    for ep, u in chains:
        P(f"  CHAIN {len(u.source_line_ids)} lines: {ep.episode_id} {u.unit_id} {u.start_s}-{u.end_s} | {u.text}")
    P("\nALL GLUED PAIRS (spot-check about 20):")
    for i, (ep, p) in enumerate(acc, 1):
        P(f"{i:3d}. {ep.episode_id} {p.a_id[5:]}->{p.b_id[5:]} gap {p.gap_s}s | {p.speaker_a}: {p.text_a}  ||  {p.text_b}")
    P("\nREJECTED ELLIPSIS PAIRS (" + str(len(rej)) + "; first 60 shown, all in glue_pairs.csv):")
    for i, (ep, p) in enumerate(rej[:60], 1):
        P(f"{i:3d}. {ep.episode_id} {p.a_id[5:]}->{p.b_id[5:]} [{p.reason}] gap {p.gap_s}s | {p.speaker_a}: {p.text_a}  ||  {p.speaker_b}: {p.text_b}")

    P("\n--- 6. THE WORD 'CLIFFHANGER' IN THE TEXT ---")
    script = [m for m in cliff_rows if m["where"] in ("camera", "action", "sfx", "dialogue")]
    P(f"script mentions: {len(script)} in {len({m['episode_id'] for m in script})}/{n} episodes, by block kind {dict(Counter(m['where'] for m in script))}")
    P(f"within last {ENDING_WINDOW_S:.0f}s of the episode: {sum(bool(m['in_ending']) for m in script)}; elsewhere: {sum(not m['in_ending'] for m in script)}")
    for m in script:
        P(f"  {m['episode_id']} {m['where']} {m['start_s']}-{m['end_s']} ({m['offset_from_end_s']}s before end)" + ("" if m["in_ending"] else "  <-- NOT at the end"))
    P(f"logline mentions: {sum(m['where'] == 'logline' for m in cliff_rows)}; continuity-note mentions: {sum(m['where'] == 'continuity_note' for m in cliff_rows)}")

    P("\n--- 7. FIRST SPOKEN LINE ---")
    fs = [r["first_spoken_s"] for r in rows if r["first_spoken_s"] is not None]
    P(_fmt_summary("first_spoken_s", fs))
    P(f"after 1s: {sum(x > 1 for x in fs)}, after 3s: {sum(x > 3 for x in fs)}, after 5s: {sum(x > 5 for x in fs)} of {len(fs)}; episodes with no dialogue: {n - len(fs)}")
    P("latest openers: " + ", ".join(f"{r['episode_id']} {r['first_spoken_s']}s" for r in sorted(rows, key=lambda r: -(r['first_spoken_s'] or 0))[:5]))
    P(_fmt_summary("first non-camera event_s", [r["first_noncamera_s"] for r in rows]))

    P("\n--- 8. ENDING SIGNALS (code-checkable only; NOT a story judgment) ---")
    P("type priority: self-labeled 'cliffhanger' word in last 10s > last spoken line is a question > cut/fade-to-black wording in last 5s > last non-camera block is dialogue > action/sfx")
    P(f"ending_type: {dict(Counter(r['ending_type'] for r in rows))}")
    P(f"last block kind: {dict(Counter(r['last_block_kind'] for r in rows))}; last non-camera block kind: {dict(Counter(r['last_noncamera_kind'] for r in rows))}")
    P(f"cut/fade-to-black wording in last 5s: {sum(r['end_cut_to_black'] for r in rows)}; last spoken line is a question: {sum(r['end_last_line_question'] for r in rows)}; "
      f"last spoken line ends in ellipsis: {sum(r['end_last_line_ellipsis'] for r in rows)}")

    P("\n--- 9. TOKEN ESTIMATES (ESTIMATES, no tokenizer available offline) ---")
    P("method: (a) raw file characters / 4; (b) whitespace words x 1.33; (c) compact view = block text + speaker + ~12 chars of ID/time per block + logline + notes, / 4.")
    for k in ("est_tokens_file_chars4", "est_tokens_file_words", "est_tokens_compact_chars4"):
        P(_fmt_summary(k, [r[k] for r in rows]) + f", total {sum(r[k] for r in rows)}")

    P("\n--- 10. REPEATED / NEAR-DUPLICATE LINES ACROSS EPISODES ---")
    npair = sum(d["has_prev"] for d in dups.values())
    P(f"utterances of >= {DUP_MIN_WORDS} words; near = SequenceMatcher ratio >= {DUP_THRESHOLD} on lowercase letters/digits")
    P(f"adjacent pairs ({npair}): exact {sum(d['dup_exact'] for d in dups.values() if d['has_prev'])}, near {sum(d['dup_near'] for d in dups.values() if d['has_prev'])}; "
      f"episodes with >=1 duplicate vs previous: {sum(1 for d in dups.values() if d['has_prev'] and d['dup_exact'] + d['dup_near'])}")
    rep = repeated_lines(episodes)
    P(f"corpus-wide: {rep['repeated']}/{rep['distinct']} distinct utterance texts occur in 2+ episodes (any length); by word count {rep['repeated_by_words']} (6 = 6+)")
    cp = sum(d["has_ctrl"] for d in dups.values())
    P(f"control pairs N vs N-{CONTROL_OFFSET} ({cp}): exact {sum(d['ctrl_exact'] for d in dups.values() if d['has_ctrl'])}, near {sum(d['ctrl_near'] for d in dups.values() if d['has_ctrl'])}")
    rc = {k: d["recap_best"] for k, d in dups.items() if d["recap_best"] is not None}
    P(f"possible reworded recap openers (first {RECAP_WINDOW} vs previous last {RECAP_WINDOW} utterances, similarity >= {RECAP_THRESHOLD}): {len(rc)}: {rc}")
    for r in dup_rows[:40]:
        P(f"  {r['prev_episode_id']}->{r['episode_id']} {r['ratio']} {'EXACT' if r['exact'] else 'near '} | {r['cur_text']}")
    if len(dup_rows) > 40:
        P(f"  ... {len(dup_rows) - 40} more in duplicates_adjacent.csv")

    P("\n--- 11. CLAUDE.md SECTION 4 CLAIM CHECK ---")
    for status in ("HELD", "PARTLY", "DID NOT HOLD", "UNVERIFIED"):
        P(f"\n[{status}]")
        for c, s, e in claims:
            if s == status:
                P(f"  - {c}\n      evidence: {e}")
    return "\n".join(L)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def run_audit(input_path: str, out_dir: str) -> int:
    files = list_episode_files(input_path)
    episodes: list[ParsedEpisode] = []
    raws: dict[str, str] = {}
    failed: list[tuple[str, str]] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
            ep = parse_text(text, p.name)
            episodes.append(ep)
            raws[ep.episode_id] = text
        except (ParseError, UnicodeDecodeError, ValueError) as e:
            failed.append((p.name, str(e)))
    if not episodes:
        print("No episode could be parsed.", file=sys.stderr)
        for name, err in failed:
            print(f"  {name}: {err}", file=sys.stderr)
        return 2
    episodes.sort(key=lambda e: e.episode_id)
    rows = [episode_row(ep, raws[ep.episode_id]) for ep in episodes]
    labels = label_stats(episodes)
    dup_rows, dups = adjacent_duplicates(episodes)
    cliff_rows = [m for ep in episodes for m in cliffhanger_mentions(ep)]
    claims = claim_checks(rows, episodes, labels, dups, len(failed))
    report = render_report(rows, episodes, failed, labels, dups, dup_rows, claims, cliff_rows)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "audit_report.txt").write_text(report, encoding="utf-8")
    write_csv(out / "episodes.csv", rows)
    write_csv(out / "glue_pairs.csv", [
        {"episode_id": ep.episode_id, "a_id": p.a_id, "b_id": p.b_id, "accepted": p.accepted, "reason": p.reason, "gap_s": p.gap_s,
         "speaker_a": p.speaker_a, "speaker_b": p.speaker_b, "text_a": p.text_a, "text_b": p.text_b}
        for ep in episodes for p in ep.audit.glue_pairs])
    write_csv(out / "camera_gaps.csv", [
        {"episode_id": ep.episode_id, "after": g.a_id, "before": g.b_id, "gap_start_s": g.start_s, "gap_end_s": g.end_s,
         "size_s": g.size_s, "other_blocks_active": g.activity_during} for ep in episodes for g in ep.audit.camera_gaps])
    write_csv(out / "cliffhanger_mentions.csv", cliff_rows)
    write_csv(out / "duplicates_adjacent.csv", dup_rows)
    write_csv(out / "label_tokens.csv", (
        [{"token": t, "count": c, "class": "modifier"} for t, c in labels["modifier_tokens"].most_common()]
        + [{"token": t, "count": c, "class": "tone"} for t, c in labels["tone_tokens"].most_common()]))
    print(report)
    print(f"\nWrote report and CSVs to {out}/")
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="promofinder audit", description="Stage 1 data audit over all episode files.")
    ap.add_argument("--input", required=True, help="directory of episode .txt files, or one file")
    ap.add_argument("--out", default="outputs/audit", help="output directory (gitignored)")
    a = ap.parse_args(argv)
    return run_audit(a.input, a.out)
