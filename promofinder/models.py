"""Pydantic schemas for Stage 1 (CLAUDE.md section 8).

Two layers:
  * ``Block``  - the ORIGINAL record, exactly as read from the file. Frozen, never edited.
  * ``Unit``   - the derived layer (shots, actions, sfx, utterance groups) built from blocks.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

BlockKind = Literal["camera", "action", "sfx", "dialogue"]
UnitKind = Literal["utterance", "action", "sfx", "shot"]


class Block(BaseModel):
    """One original ``**LABEL:**`` record. Frozen: derived layers must not mutate it."""

    model_config = ConfigDict(frozen=True)

    block_id: str                 # e.g. E001-B007 (1-based position in file order)
    kind: BlockKind
    label_raw: str                # label text exactly as written, without the ** and colon
    speaker_label: Optional[str] = None   # dialogue only: name part of the label
    tone: tuple[str, ...] = ()            # dialogue only: parenthesised tokens not in the modifier set
    modifiers: tuple[str, ...] = ()       # dialogue only: parenthesised tokens in the modifier set
    is_vo: bool = False
    is_os: bool = False
    start_s: float
    end_s: float
    text: str                     # text exactly as in the file (dialogue keeps its quote marks)
    line_no: int                  # 1-based line number of the label line in the source file
    scene_index: int              # 1-based scene number this block sits in


class Scene(BaseModel):
    index: int
    heading: str = ""
    start_s: Optional[float] = None
    end_s: Optional[float] = None
    line_no: int = 0


class Unit(BaseModel):
    """Derived unit. ``source_line_ids`` link back to original block ids."""

    unit_id: str                  # e.g. E001-U007, E001-S003, E001-A004, E001-F010
    kind: UnitKind
    start_s: float
    end_s: float
    text: str                     # utterance: unquoted fragments joined by one space
    speaker_label: Optional[str] = None
    tone: list[str] = Field(default_factory=list)
    is_vo: bool = False
    is_os: bool = False
    source_line_ids: list[str]
    shot_id: Optional[str] = None  # shot covering the unit's start; None inside a camera gap
    scene_index: int = 0


class Span(BaseModel):
    """A gap or overlap between two blocks."""

    a_id: str
    b_id: str
    start_s: float
    end_s: float
    size_s: float
    activity_during: bool = False  # gaps only: a non-camera block overlaps the gap


class UnparsedLine(BaseModel):
    line_no: int
    reason: str
    text: str


class GluePair(BaseModel):
    a_id: str
    b_id: str
    gap_s: float
    speaker_a: str
    speaker_b: str
    accepted: bool
    reason: str                   # "glued" or why the ellipsis pair was rejected
    text_a: str
    text_b: str


class EpisodeAudit(BaseModel):
    unparsed_lines: list[UnparsedLine] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    nonstandard_time_strings: list[str] = Field(default_factory=list)
    camera_gaps: list[Span] = Field(default_factory=list)
    camera_overlaps: list[Span] = Field(default_factory=list)
    dialogue_overlaps: list[Span] = Field(default_factory=list)
    glue_pairs: list[GluePair] = Field(default_factory=list)  # every ellipsis pair, accepted or not
    blocks_out_of_start_order: int = 0
    camera_first_start_s: Optional[float] = None
    camera_last_end_s: Optional[float] = None


class ParsedEpisode(BaseModel):
    episode_id: str               # E001
    episode_number: Optional[int] = None
    source_name: str
    duration_s: float             # end of the last scene as declared; falls back to last block end
    last_block_end_s: float
    logline: Optional[str] = None
    continuity_note: Optional[str] = None
    format_variant: dict[str, str] = Field(default_factory=dict)
    scenes: list[Scene]
    blocks: list[Block]           # original layer
    units: list[Unit]             # derived layer
    audit: EpisodeAudit
