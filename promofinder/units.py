"""Derived unit layer (CLAUDE.md section 6a). Pure functions; Blocks are never modified.

Glue rule (a hypothesis, tested in the audit): a dialogue line ending in an ellipsis followed
by the NEXT dialogue line (any speaker, by start time) starting with an ellipsis, same speaker
name, gap <= 1.5s. Rejected ellipsis pairs are kept, with the reason, so the audit can show them.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import Block, GluePair, Unit

MAX_GLUE_GAP_S = 1.5
INTERRUPT_LOOKAHEAD = 3        # how many later dialogue lines to scan for an "interrupted" pair
INTERRUPT_MAX_GAP_S = 3.0
TOL = 0.005                    # times have 2 decimals, so 0.005 means "exactly equal"

_QUOTE_PAIRS = {'"': '"', "“": "”", "'": "'", "‘": "’"}


def unwrap_quotes(text: str) -> str:
    """Remove ONE pair of wrapping quote marks, if present. Everything else is untouched."""
    t = text.strip()
    if len(t) >= 2 and t[0] in _QUOTE_PAIRS and t[-1] == _QUOTE_PAIRS[t[0]]:
        return t[1:-1].strip()
    return t


def ends_with_ellipsis(text: str) -> bool:
    t = unwrap_quotes(text).rstrip()
    return t.endswith("...") or t.endswith("…")


def starts_with_ellipsis(text: str) -> bool:
    t = unwrap_quotes(text).lstrip()
    return t.startswith("...") or t.startswith("…")


def speaker_key(label: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().upper())


def _idx(block_id: str) -> int:
    return int(block_id.rsplit("-B", 1)[1])


def _episode_prefix(block_id: str) -> str:
    return block_id.rsplit("-B", 1)[0]


def sorted_dialogue(blocks: list[Block]) -> list[Block]:
    return sorted((b for b in blocks if b.kind == "dialogue"), key=lambda b: (b.start_s, b.end_s, _idx(b.block_id)))


def find_glue_pairs(blocks: list[Block], max_gap_s: float = MAX_GLUE_GAP_S) -> list[GluePair]:
    """Every ellipsis-ended line paired with its continuation candidate, accepted or rejected."""
    dlg = sorted_dialogue(blocks)
    out: list[GluePair] = []
    for i, a in enumerate(dlg):
        if not ends_with_ellipsis(a.text):
            continue
        nxt = dlg[i + 1] if i + 1 < len(dlg) else None
        if nxt is not None and starts_with_ellipsis(nxt.text):
            gap = round(nxt.start_s - a.end_s, 2)
            same = speaker_key(a.speaker_label) == speaker_key(nxt.speaker_label)
            ok_gap = gap <= max_gap_s
            if same and ok_gap:
                reason = "glued"
            else:
                reason = "+".join(r for r, bad in (("different_speaker", not same), ("gap_too_large", not ok_gap)) if bad)
            out.append(GluePair(a_id=a.block_id, b_id=nxt.block_id, gap_s=gap, speaker_a=a.speaker_label or "",
                                speaker_b=nxt.speaker_label or "", accepted=same and ok_gap, reason=reason,
                                text_a=a.text, text_b=nxt.text))
            continue
        # not adjacent: is there a same-speaker continuation a few lines later?
        for b in dlg[i + 2 : i + 2 + INTERRUPT_LOOKAHEAD]:
            gap = round(b.start_s - a.end_s, 2)
            if starts_with_ellipsis(b.text) and speaker_key(a.speaker_label) == speaker_key(b.speaker_label) and gap <= INTERRUPT_MAX_GAP_S:
                out.append(GluePair(a_id=a.block_id, b_id=b.block_id, gap_s=gap, speaker_a=a.speaker_label or "",
                                    speaker_b=b.speaker_label or "", accepted=False,
                                    reason="interrupted_by_other_line", text_a=a.text, text_b=b.text))
                break
    return out


def build_units(blocks: list[Block], glue_pairs: list[GluePair]) -> list[Unit]:
    """Derived layer: one unit per shot/action/sfx block, one per utterance group."""
    accepted = {(g.a_id, g.b_id) for g in glue_pairs if g.accepted}
    units: list[Unit] = []

    groups: list[list[Block]] = []
    for b in sorted_dialogue(blocks):
        if groups and (groups[-1][-1].block_id, b.block_id) in accepted:
            groups[-1].append(b)
        else:
            groups.append([b])
    for g in groups:
        first = g[0]
        tone: list[str] = []
        for b in g:
            for t in b.tone:
                if t not in tone:
                    tone.append(t)
        units.append(Unit(
            unit_id=f"{_episode_prefix(first.block_id)}-U{_idx(first.block_id):03d}", kind="utterance",
            start_s=first.start_s, end_s=max(b.end_s for b in g),
            text=" ".join(unwrap_quotes(b.text) for b in g), speaker_label=first.speaker_label, tone=tone,
            is_vo=any(b.is_vo for b in g), is_os=any(b.is_os for b in g),
            source_line_ids=[b.block_id for b in g], scene_index=first.scene_index,
        ))

    prefix_for = {"camera": ("shot", "S"), "action": ("action", "A"), "sfx": ("sfx", "F")}
    for b in blocks:
        if b.kind in prefix_for:
            kind, letter = prefix_for[b.kind]
            units.append(Unit(
                unit_id=f"{_episode_prefix(b.block_id)}-{letter}{_idx(b.block_id):03d}", kind=kind,
                start_s=b.start_s, end_s=b.end_s, text=b.text, source_line_ids=[b.block_id],
                scene_index=b.scene_index,
            ))

    rank = {"shot": 0, "action": 1, "sfx": 2, "utterance": 3}
    units.sort(key=lambda u: (u.start_s, rank[u.kind], u.unit_id))
    return assign_shots(units)


def assign_shots(units: list[Unit]) -> list[Unit]:
    """Set shot_id on every unit to the shot covering its start (None inside a camera gap)."""
    shots = [u for u in units if u.kind == "shot"]
    out: list[Unit] = []
    for u in units:
        if u.kind == "shot":
            out.append(u.model_copy(update={"shot_id": u.unit_id}))
            continue
        sid = next((s.unit_id for s in shots if s.start_s - TOL <= u.start_s < s.end_s - TOL), None)
        out.append(u.model_copy(update={"shot_id": sid}))
    return out
