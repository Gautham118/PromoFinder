"""Timeline checks (pure): camera gaps/overlaps, dialogue overlaps, shot-boundary crossings.

Never assumes a gapless timeline; it measures and reports.
"""
from __future__ import annotations

from typing import Optional

from .models import Block, EpisodeAudit, Scene, Span

TOL = 0.005
CROSS_EPS = 0.05   # a shot boundary within 0.05s of a line's start/end does not count as "inside" it


def _idx(block_id: str) -> int:
    return int(block_id.rsplit("-B", 1)[1])


def camera_gaps_overlaps(blocks: list[Block]) -> tuple[list[Span], list[Span]]:
    cams = sorted((b for b in blocks if b.kind == "camera"), key=lambda b: (b.start_s, b.end_s, _idx(b.block_id)))
    others = [b for b in blocks if b.kind != "camera"]
    gaps: list[Span] = []
    overlaps: list[Span] = []
    max_end_block: Optional[Block] = None
    for c in cams:
        if max_end_block is not None:
            d = round(c.start_s - max_end_block.end_s, 2)
            if d > TOL:
                active = any(o.start_s < c.start_s - TOL and o.end_s > max_end_block.end_s + TOL for o in others)
                gaps.append(Span(a_id=max_end_block.block_id, b_id=c.block_id, start_s=max_end_block.end_s,
                                 end_s=c.start_s, size_s=d, activity_during=active))
            elif d < -TOL:
                overlaps.append(Span(a_id=max_end_block.block_id, b_id=c.block_id, start_s=c.start_s,
                                     end_s=min(c.end_s, max_end_block.end_s), size_s=round(-d, 2)))
        if max_end_block is None or c.end_s > max_end_block.end_s:
            max_end_block = c
    return gaps, overlaps


def dialogue_overlaps(blocks: list[Block]) -> list[Span]:
    dlg = sorted((b for b in blocks if b.kind == "dialogue"), key=lambda b: (b.start_s, b.end_s, _idx(b.block_id)))
    out: list[Span] = []
    latest: Optional[Block] = None
    for b in dlg:
        if latest is not None and b.start_s < latest.end_s - TOL:
            out.append(Span(a_id=latest.block_id, b_id=b.block_id, start_s=b.start_s,
                            end_s=min(b.end_s, latest.end_s), size_s=round(min(b.end_s, latest.end_s) - b.start_s, 2)))
        if latest is None or b.end_s > latest.end_s:
            latest = b
    return out


def shot_boundaries(blocks: list[Block]) -> list[float]:
    ts: set[float] = set()
    for b in blocks:
        if b.kind == "camera":
            ts.add(b.start_s)
            ts.add(b.end_s)
    return sorted(ts)


def boundaries_inside(start_s: float, end_s: float, boundaries: list[float]) -> list[float]:
    """Shot boundaries strictly inside (start_s, end_s), ignoring ones within CROSS_EPS of the ends."""
    return [t for t in boundaries if start_s + CROSS_EPS < t < end_s - CROSS_EPS]


def out_of_start_order(blocks: list[Block]) -> int:
    """How many blocks start earlier than the block before them in file order."""
    return sum(1 for a, b in zip(blocks, blocks[1:]) if b.start_s < a.start_s - TOL)


def timeline_audit(blocks: list[Block], scenes: list[Scene], audit: EpisodeAudit) -> None:
    """Fill the timeline parts of ``audit`` in place."""
    audit.camera_gaps, audit.camera_overlaps = camera_gaps_overlaps(blocks)
    audit.dialogue_overlaps = dialogue_overlaps(blocks)
    audit.blocks_out_of_start_order = out_of_start_order(blocks)
    cams = [b for b in blocks if b.kind == "camera"]
    if cams:
        audit.camera_first_start_s = min(c.start_s for c in cams)
        audit.camera_last_end_s = max(c.end_s for c in cams)
    else:
        audit.warnings.append("no CAMERA blocks found")
    for a, b in zip(scenes, scenes[1:]):
        if a.end_s is not None and b.start_s is not None and b.start_s < a.end_s - TOL:
            audit.warnings.append(f"scene {b.index} starts before scene {a.index} ends (times may reset per scene)")
