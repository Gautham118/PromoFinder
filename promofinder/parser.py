"""Stage 1 parser: one screenplay .txt -> ParsedEpisode (CLAUDE.md sections 4, 6a, 8).

Rules: case-insensitive; every block gets a stable ID from its position in the file;
original records (Block) are never edited; anything unreadable is recorded in
``audit.unparsed_lines`` (loud), and a file with no script markers raises ParseError.
No timestamps are ever invented.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .models import Block, EpisodeAudit, ParsedEpisode, Scene, UnparsedLine
from .timeline import timeline_audit
from .units import build_units, find_glue_pairs

# ---- structural line patterns (all case-insensitive) --------------------------------
RE_EP_NUM = re.compile(r"^\s*(episode\s+number)\s*:\s*(.*?)\s*$", re.I)
RE_LOGLINE = re.compile(r"^\s*(episode\s+logline)\s*:\s*(.*?)\s*$", re.I)
RE_CONT = re.compile(r"^\s*(continuity\s+notes?)\s*:\s*(.*?)\s*$", re.I)
RE_SCRIPT_START = re.compile(r"^\s*-{2,}\s*script\s+start\s*-{2,}\s*$", re.I)
RE_SCRIPT_END = re.compile(r"^\s*-{2,}\s*script\s+end\s*-{2,}\s*$", re.I)
RE_SCENE = re.compile(r"^\s*(scene)\s+(\d+)\s*$", re.I)
RE_SCENE_HEADING = re.compile(r"^\s*(scene\s+heading)\s*:\s*(.*?)\s*$", re.I)
RE_SCENE_START = re.compile(r"^\s*(scene\s+start)\s*:\s*(\S+)\s*$", re.I)
RE_SCENE_END = re.compile(r"^\s*(scene\s+end)\s*:\s*(\S+)\s*$", re.I)
RE_ACTIONS = re.compile(r"^\s*(actions?\s+and\s+dialogue)\s*:\s*$", re.I)
RE_LABEL = re.compile(r"^\s*\*\*(.+?):\*\*\s*$")
RE_TIMES = re.compile(
    r"^\s*start\s*:\s*\(?\s*([\d:.]+)\s*\)?\s*\|\s*end\s*:\s*\(?\s*([\d:.]+)\s*\)?\s*$", re.I
)
RE_TS = re.compile(r"^(?:(\d+):)?(\d+):(\d{1,2})(?:\.(\d+))?$")
RE_TS_STANDARD = re.compile(r"^\d{2}:\d{2}\.\d{2}$")
RE_PAREN = re.compile(r"\(([^()]*)\)")
RE_FILE_NUM = re.compile(r"(\d+)")

# Known speaker-label modifiers (normalised: lowercase letters only). Everything else in
# parentheses is treated as a tone tag. Assumption from CLAUDE.md section 4, checked in audit.
MODIFIERS = {"os", "oc", "vo", "offscreen", "continues", "contd", "continued", "continuing", "cont"}
OS_MODS = {"os", "oc", "offscreen"}
VO_MODS = {"vo"}

NON_DIALOGUE = {
    "camera": "camera",
    "action": "action",
    "actions": "action",
    "soundfx": "sfx",
    "sfx": "sfx",
    "soundeffects": "sfx",
    "soundeffect": "sfx",
}


class ParseError(Exception):
    """Raised when a file cannot be treated as an episode at all."""


# ---- small pure helpers ---------------------------------------------------------------
def parse_timestamp(s: str) -> Optional[float]:
    """'MM:SS.ss' (or H:MM:SS.ss) -> seconds rounded to 2 decimals; None if unreadable."""
    m = RE_TS.match(s.strip())
    if not m:
        return None
    h, mm, ss, frac = m.groups()
    sec = int(h or 0) * 3600 + int(mm) * 60 + int(ss) + (float(f"0.{frac}") if frac else 0.0)
    return round(sec, 2)


def casing_style(s: str) -> str:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return "none"
    if all(c.isupper() for c in letters):
        return "UPPER"
    if all(c.islower() for c in letters):
        return "lower"
    return "Mixed"


def norm_token(t: str) -> str:
    return re.sub(r"[^a-z]", "", t.lower())


def parse_label(label_raw: str) -> dict:
    """Split a label into kind, speaker name, tone tags and modifiers."""
    name = re.sub(r"\s+", " ", RE_PAREN.sub("", label_raw)).strip()
    tokens = [
        t.strip()
        for g in RE_PAREN.findall(label_raw)
        for t in re.split(r"[,/]", g)
        if t.strip()
    ]
    key = norm_token(name)
    if key in NON_DIALOGUE:
        return {"kind": NON_DIALOGUE[key], "speaker": None, "tone": (), "modifiers": (),
                "is_os": False, "is_vo": False}
    mods = tuple(t for t in tokens if norm_token(t) in MODIFIERS)
    tone = tuple(t for t in tokens if norm_token(t) not in MODIFIERS)
    mod_keys = {norm_token(t) for t in mods}
    return {"kind": "dialogue", "speaker": name, "tone": tone, "modifiers": mods,
            "is_os": bool(mod_keys & OS_MODS), "is_vo": bool(mod_keys & VO_MODS)}


def episode_id_from(number: Optional[int], source_name: str) -> tuple[str, Optional[int], Optional[str]]:
    """Return (episode_id, number, warning). Header number wins; filename is the fallback."""
    m = RE_FILE_NUM.search(Path(source_name).name)
    file_num = int(m.group(1)) if m else None
    if number is not None:
        warn = None
        if file_num is not None and file_num != number:
            warn = f"header episode number {number} != filename number {file_num}"
        return f"E{number:03d}", number, warn
    if file_num is not None:
        return f"E{file_num:03d}", file_num, "no episode number in header; used filename number"
    raise ParseError(f"{source_name}: no episode number in header or filename")


# ---- main entry points ------------------------------------------------------------------
def parse_text(text: str, source_name: str = "<memory>") -> ParsedEpisode:
    has_bom = text.startswith("\ufeff")
    crlf = "\r\n" in text
    lines = text.lstrip("\ufeff").splitlines()

    start_idx = next((i for i, ln in enumerate(lines) if RE_SCRIPT_START.match(ln)), None)
    end_idx = next((i for i, ln in enumerate(lines) if RE_SCRIPT_END.match(ln)), None)
    if start_idx is None or end_idx is None or end_idx < start_idx:
        raise ParseError(f"{source_name}: missing or misordered SCRIPT START / SCRIPT END markers")

    audit = EpisodeAudit()
    fv: dict[str, str] = {"line_endings": "CRLF" if crlf else "LF", "bom": "yes" if has_bom else "no"}

    # header
    number: Optional[int] = None
    logline: Optional[str] = None
    cont: Optional[str] = None
    for i in range(start_idx):
        s = lines[i].strip()
        if not s:
            continue
        if m := RE_EP_NUM.match(s):
            fv["episode_number_label"] = casing_style(m.group(1))
            dm = re.search(r"\d+", m.group(2))
            number = int(dm.group()) if dm else None
        elif m := RE_LOGLINE.match(s):
            fv["logline_label"] = casing_style(m.group(1))
            logline = m.group(2) or None
        elif m := RE_CONT.match(s):
            fv["continuity_label"] = casing_style(m.group(1))
            cont = m.group(2) or None
        else:
            audit.unparsed_lines.append(UnparsedLine(line_no=i + 1, reason="unrecognised header line", text=s[:120]))
    fv["has_logline"] = "yes" if logline else "no"
    fv["has_continuity"] = "yes" if cont else "no"
    episode_id, ep_number, warn = episode_id_from(number, source_name)
    if warn:
        audit.warnings.append(warn)

    # script body
    scenes: list[Scene] = []
    blocks: list[Block] = []
    i = start_idx + 1
    while i < end_idx:
        raw = lines[i]
        s = raw.strip()
        n = i + 1
        if not s:
            i += 1
            continue
        if m := RE_SCENE.match(s):
            fv["scene_marker"] = casing_style(m.group(1))
            scenes.append(Scene(index=len(scenes) + 1, line_no=n))
            i += 1
            continue
        if m := RE_SCENE_HEADING.match(s):
            if scenes:
                scenes[-1].heading = m.group(2)
                fv["scene_heading_label"] = casing_style(m.group(1))
                fv["scene_heading_value"] = casing_style(m.group(2))
            else:
                audit.unparsed_lines.append(UnparsedLine(line_no=n, reason="scene heading before any SCENE line", text=s[:120]))
            i += 1
            continue
        if m := (RE_SCENE_START.match(s) or RE_SCENE_END.match(s)):
            is_start = bool(RE_SCENE_START.match(s))
            ts = parse_timestamp(m.group(2))
            if not scenes or ts is None:
                audit.unparsed_lines.append(UnparsedLine(line_no=n, reason="unreadable scene time or no scene", text=s[:120]))
            else:
                fv["scene_start_label" if is_start else "scene_end_label"] = casing_style(m.group(1))
                if is_start:
                    scenes[-1].start_s = ts
                else:
                    scenes[-1].end_s = ts
            i += 1
            continue
        if m := RE_ACTIONS.match(s):
            fv["actions_label"] = casing_style(m.group(1))
            i += 1
            continue
        if m := RE_LABEL.match(s):
            label_raw = m.group(1).strip()
            # next non-blank line must be the Start/End line
            j = i + 1
            while j < end_idx and not lines[j].strip():
                j += 1
            tm = RE_TIMES.match(lines[j]) if j < end_idx else None
            t0 = parse_timestamp(tm.group(1)) if tm else None
            t1 = parse_timestamp(tm.group(2)) if tm else None
            if tm is None or t0 is None or t1 is None:
                audit.unparsed_lines.append(UnparsedLine(line_no=n, reason="label without readable Start/End line", text=s[:120]))
                i += 1
                continue
            for g in (tm.group(1), tm.group(2)):
                if not RE_TS_STANDARD.match(g):
                    audit.nonstandard_time_strings.append(g)
            k = j + 1
            text_lines: list[str] = []
            while k < end_idx and lines[k].strip() and not RE_LABEL.match(lines[k]) and not RE_SCENE.match(lines[k].strip()):
                text_lines.append(lines[k].strip())
                k += 1
            if not scenes:
                audit.warnings.append(f"line {n}: block before any SCENE line; placed in implicit scene 1")
                scenes.append(Scene(index=1, line_no=n))
            if t1 < t0:
                audit.warnings.append(f"line {n}: end before start ({t0} > {t1})")
            info = parse_label(label_raw)
            blocks.append(Block(
                block_id=f"{episode_id}-B{len(blocks) + 1:03d}",
                kind=info["kind"], label_raw=label_raw, speaker_label=info["speaker"],
                tone=info["tone"], modifiers=info["modifiers"], is_vo=info["is_vo"], is_os=info["is_os"],
                start_s=t0, end_s=t1, text=" ".join(text_lines), line_no=n, scene_index=scenes[-1].index,
            ))
            if not text_lines:
                audit.warnings.append(f"line {n}: block {blocks[-1].block_id} has no text")
            i = k
            continue
        audit.unparsed_lines.append(UnparsedLine(line_no=n, reason="line outside any recognised structure", text=s[:120]))
        i += 1

    # anything after SCRIPT END is reported, not ignored
    for q in range(end_idx + 1, len(lines)):
        if lines[q].strip():
            audit.unparsed_lines.append(UnparsedLine(line_no=q + 1, reason="text after SCRIPT END", text=lines[q].strip()[:120]))

    if not blocks:
        raise ParseError(f"{source_name}: no blocks could be parsed")

    last_block_end = max(b.end_s for b in blocks)
    scene_ends = [sc.end_s for sc in scenes if sc.end_s is not None]
    duration = max(scene_ends) if scene_ends else last_block_end
    if not scene_ends:
        audit.warnings.append("no scene end times; duration_s taken from last block end")
    for sc in scenes:
        if sc.start_s is None or sc.end_s is None:
            audit.warnings.append(f"scene {sc.index} is missing a start or end time")

    glue_pairs = find_glue_pairs(blocks)
    units = build_units(blocks, glue_pairs)
    timeline_audit(blocks, scenes, audit)
    audit.glue_pairs = glue_pairs

    return ParsedEpisode(
        episode_id=episode_id, episode_number=ep_number, source_name=Path(source_name).name,
        duration_s=duration, last_block_end_s=last_block_end, logline=logline, continuity_note=cont,
        format_variant=fv, scenes=scenes, blocks=blocks, units=units, audit=audit,
    )


def parse_file(path: str | Path) -> ParsedEpisode:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ParseError(f"{p.name}: not valid UTF-8 ({e})") from e
    return parse_text(text, p.name)


def list_episode_files(input_path: str | Path) -> list[Path]:
    p = Path(input_path)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(p.rglob("*.txt"))
    raise ParseError(f"input path does not exist: {p}")
