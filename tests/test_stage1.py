"""Stage 1 tests. They run ONLY on the synthetic sample (or text derived from it). No API, no pack."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from promofinder.audit import (cliffhanger_mentions, dialogue_crossings, dup_pairs, ending_signals, estimate_tokens,
                               first_times, glue_stats, normalize_for_dup, run_audit)
from promofinder.parser import ParseError, parse_label, parse_text, parse_timestamp
from promofinder.units import (build_units, ends_with_ellipsis, find_glue_pairs, speaker_key, starts_with_ellipsis,
                               unwrap_quotes)

SAMPLE = Path(__file__).resolve().parent.parent / "sample" / "episode-001-sample.txt"


@pytest.fixture(scope="module")
def raw() -> str:
    return SAMPLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ep(raw):
    return parse_text(raw, SAMPLE.name)


def block(ep, n):
    return next(b for b in ep.blocks if b.block_id.endswith(f"-B{n:03d}"))


# ---- parsing basics ---------------------------------------------------------------------
def test_sample_parses_cleanly(ep):
    assert ep.episode_id == "E001" and ep.episode_number == 1
    assert len(ep.blocks) == 43 and len(ep.scenes) == 2
    assert ep.duration_s == 65.5 == ep.last_block_end_s
    assert not ep.audit.unparsed_lines and not ep.audit.warnings and not ep.audit.nonstandard_time_strings
    assert ep.logline and ep.continuity_note


def test_timestamps():
    assert parse_timestamp("01:04.50") == 64.5
    assert parse_timestamp("00:00.00") == 0.0
    assert parse_timestamp("1:02:03.5") == 3723.5
    assert parse_timestamp("garbage") is None


def test_label_parsing_variants():
    a = parse_label("NORA (O.S.) (Shaking)")
    assert a["kind"] == "dialogue" and a["is_os"] and a["modifiers"] == ("O.S.",) and a["tone"] == ("Shaking",)
    b = parse_label("CAPTAIN HOLT (O.S. / Low, Threatening)")
    assert b["is_os"] and b["modifiers"] == ("O.S.",) and b["tone"] == ("Low", "Threatening")
    c = parse_label("TECHNICIAN (V.O.)")
    assert c["is_vo"] and not c["tone"]
    d = parse_label("NORA (Hushed, Fearful)")
    assert d["tone"] == ("Hushed", "Fearful") and not d["modifiers"]
    e = parse_label("sound fx")
    assert e["kind"] == "sfx" and e["speaker"] is None
    assert parse_label("Camera")["kind"] == "camera"


def test_block_fields(ep):
    b6 = block(ep, 6)
    assert b6.kind == "dialogue" and b6.speaker_label == "NORA" and b6.text == '"He is not coming back."'
    assert (b6.start_s, b6.end_s) == (7.5, 9.0) and b6.scene_index == 1
    assert block(ep, 34).scene_index == 2
    assert block(ep, 18).is_vo and block(ep, 16).is_os and block(ep, 34).is_os


# ---- stable IDs, immutability, case-insensitivity -------------------------------------------
def test_ids_are_stable_and_unique(raw, ep):
    again = parse_text(raw, SAMPLE.name)
    assert [b.block_id for b in ep.blocks] == [b.block_id for b in again.blocks]
    assert [u.unit_id for u in ep.units] == [u.unit_id for u in again.units]
    ids = [u.unit_id for u in ep.units]
    assert len(ids) == len(set(ids))
    assert "E001-U019" in ids and "E001-S001" in ids and "E001-F002" in ids and "E001-A004" in ids


def test_original_blocks_are_frozen(ep):
    with pytest.raises(ValidationError):
        block(ep, 6).text = "changed"


def test_building_units_does_not_touch_blocks(ep):
    before = [b.model_dump() for b in ep.blocks]
    build_units(ep.blocks, find_glue_pairs(ep.blocks))
    assert [b.model_dump() for b in ep.blocks] == before


def test_case_insensitive_structure(raw):
    lines = []
    for ln in raw.splitlines():
        low = ln.lower()
        if low.startswith(("scene ", "actions and dialogue", "episode ", "continuity", "--- script")):
            ln = ln.upper() if not low.startswith(("episode logline", "continuity")) else ln.split(":")[0].upper() + ":" + ln.split(":", 1)[1]
        if ln.startswith("**") or ln.startswith("Start:"):
            ln = ln.upper().replace("**", "**") if ln.startswith("**") else ln.replace("Start:", "START:").replace("End:", "END:")
        lines.append(ln)
    up = parse_text("\n".join(lines), "episode-001-upper.txt")
    base = parse_text(raw, SAMPLE.name)
    assert len(up.blocks) == len(base.blocks) and len(up.scenes) == 2
    assert up.scenes[0].heading == base.scenes[0].heading
    assert up.format_variant["actions_label"] == "UPPER" and base.format_variant["actions_label"] == "Mixed"
    assert not up.audit.unparsed_lines


def test_crlf_and_bom(raw):
    e = parse_text("\ufeff" + raw.replace("\n", "\r\n"), "x-001.txt")
    assert e.format_variant["line_endings"] == "CRLF" and e.format_variant["bom"] == "yes"
    assert len(e.blocks) == 43 and not e.audit.unparsed_lines


def test_optional_header_fields_really_optional(raw):
    stripped = "\n".join(l for l in raw.splitlines() if not l.startswith(("EPISODE LOGLINE", "CONTINUITY NOTES")))
    e = parse_text(stripped, SAMPLE.name)
    assert e.logline is None and e.continuity_note is None and len(e.blocks) == 43


# ---- failing loudly ---------------------------------------------------------------------------
def test_missing_markers_raise(raw):
    with pytest.raises(ParseError):
        parse_text(raw.replace("--- SCRIPT END ---", ""), "x.txt")
    with pytest.raises(ParseError):
        parse_text("just some text\nno structure", "episode-009.txt")


def test_no_episode_number_anywhere_raises(raw):
    with pytest.raises(ParseError):
        parse_text(raw.replace("EPISODE NUMBER: Episode 1", ""), "noname.txt")


def test_filename_fallback_and_mismatch_are_reported(raw):
    e = parse_text(raw.replace("EPISODE NUMBER: Episode 1", ""), "episode-007-x.txt")
    assert e.episode_id == "E007" and any("filename" in w for w in e.audit.warnings)
    m = parse_text(raw, "episode-009-x.txt")
    assert m.episode_id == "E001" and any("!=" in w for w in m.audit.warnings)


def test_unreadable_time_is_reported_not_invented(raw):
    bad = raw.replace("Start: (00:07.50) | End: (00:09.00)", "Start: (soon) | End: (later)")
    e = parse_text(bad, SAMPLE.name)
    assert len(e.blocks) == 42
    assert any("Start/End" in u.reason for u in e.audit.unparsed_lines)
    assert any("outside any recognised structure" in u.reason for u in e.audit.unparsed_lines)  # its text line


def test_stray_line_and_trailing_text_are_reported(raw):
    e = parse_text(raw.replace("**NORA (Whispering):**", "stray words here\n\n**NORA (Whispering):**") + "\nafter the end\n", SAMPLE.name)
    reasons = [u.reason for u in e.audit.unparsed_lines]
    assert "line outside any recognised structure" in reasons and "text after SCRIPT END" in reasons


def test_nonstandard_time_shape_is_flagged(raw):
    e = parse_text(raw.replace("(00:07.50)", "(0:07.5)"), SAMPLE.name)
    assert e.audit.nonstandard_time_strings == ["0:07.5"]
    assert block(e, 6).start_s == 7.5


# ---- scenes ----------------------------------------------------------------------------------
def test_scenes_are_episode_global(ep):
    s1, s2 = ep.scenes
    assert (s1.start_s, s1.end_s, s2.start_s, s2.end_s) == (0.0, 40.0, 40.0, 65.5)
    assert s2.heading.startswith("INT. HARBOR OFFICE")


def test_scene_reset_is_warned(raw):
    e = parse_text(raw.replace("Scene start: 00:40.00", "Scene start: 00:00.00"), SAMPLE.name)
    assert any("reset" in w for w in e.audit.warnings)


# ---- ellipsis / utterance groups -----------------------------------------------------------------
def test_ellipsis_helpers():
    assert ends_with_ellipsis('"and then..."') and ends_with_ellipsis("wait…")
    assert starts_with_ellipsis('"...and so"') and starts_with_ellipsis("…and so")
    assert not ends_with_ellipsis('"It is over."') and not starts_with_ellipsis('"It is over."')
    assert unwrap_quotes('"hi"') == "hi" and unwrap_quotes("“hi”") == "hi" and unwrap_quotes("no quotes") == "no quotes"
    assert speaker_key("  captain   holt ") == "CAPTAIN HOLT"


def test_glued_pairs_and_chain(ep):
    acc = [(p.a_id[-3:], p.b_id[-3:]) for p in ep.audit.glue_pairs if p.accepted]
    assert acc == [("008", "010"), ("019", "020"), ("020", "022")]
    utts = [u for u in ep.units if u.kind == "utterance"]
    assert len(utts) == 14
    chain = next(u for u in utts if len(u.source_line_ids) == 3)
    assert chain.source_line_ids == ["E001-B019", "E001-B020", "E001-B022"]
    assert chain.text == "Ask him about the ledger... ...the one with no name... ...kept under the floorboards."
    assert (chain.start_s, chain.end_s) == (24.2, 29.0)
    two = next(u for u in utts if u.source_line_ids == ["E001-B008", "E001-B010"])
    assert (two.start_s, two.end_s) == (9.5, 14.5) and two.unit_id == "E001-U008"


def test_trailing_off_is_not_glued(ep):
    b12 = block(ep, 12)
    assert ends_with_ellipsis(b12.text)
    assert all(p.a_id != b12.block_id for p in ep.audit.glue_pairs)
    u12 = next(u for u in ep.units if u.source_line_ids == [b12.block_id])
    assert u12.kind == "utterance"
    g = glue_stats(ep)
    assert g["glued"] == 3 and g["chains_3plus"] == 1 and g["max_chain"] == 3
    assert g["trailing_off_unpaired"] == 1 and g["orphan_starts_unpaired"] == 0 and g["dialogue_without_quotes"] == 0


def test_glue_rejected_for_other_speaker(raw):
    e = parse_text(raw.replace("**CAPTAIN HOLT (Cold):**\nStart: (00:13.00)", "**COMMANDER HOLT (Cold):**\nStart: (00:13.00)"), SAMPLE.name)
    p = next(p for p in e.audit.glue_pairs if p.a_id.endswith("B008"))
    assert not p.accepted and p.reason == "different_speaker"
    assert len([u for u in e.units if u.kind == "utterance"]) == 15


def test_glue_rejected_for_large_gap(raw):
    e = parse_text(raw.replace("Start: (00:13.00) | End: (00:14.50)", "Start: (00:15.00) | End: (00:16.00)"), SAMPLE.name)
    p = next(p for p in e.audit.glue_pairs if p.a_id.endswith("B008"))
    assert not p.accepted and p.reason == "gap_too_large" and p.gap_s == 2.2


def test_every_dialogue_block_belongs_to_exactly_one_group(ep):
    ids = [i for u in ep.units if u.kind == "utterance" for i in u.source_line_ids]
    assert sorted(ids) == sorted(b.block_id for b in ep.blocks if b.kind == "dialogue")


def test_units_are_chronological_and_linked(ep):
    starts = [u.start_s for u in ep.units]
    assert starts == sorted(starts)
    block_ids = {b.block_id for b in ep.blocks}
    assert all(i in block_ids for u in ep.units for i in u.source_line_ids)


# ---- shots, gaps, overlaps, crossings ---------------------------------------------------------------
def test_shot_assignment(ep):
    by_id = {u.unit_id: u for u in ep.units}
    assert by_id["E001-S001"].shot_id == "E001-S001"
    assert by_id["E001-U006"].shot_id == "E001-S005"          # line starts inside shot B005
    assert by_id["E001-U014"].shot_id is None                 # starts inside the camera gap
    assert by_id["E001-A013"].shot_id == "E001-S011"


def test_camera_gap_and_overlap(ep):
    gap = ep.audit.camera_gaps
    assert len(gap) == 1 and (gap[0].start_s, gap[0].end_s, gap[0].size_s) == (18.0, 19.5, 1.5) and gap[0].activity_during
    ov = ep.audit.camera_overlaps
    assert len(ov) == 1 and ov[0].size_s == 0.5 and (ov[0].start_s, ov[0].end_s) == (32.5, 33.0)
    assert ep.audit.camera_first_start_s == 0.0 and ep.audit.camera_last_end_s == 65.5


def test_dialogue_overlap(ep):
    d = ep.audit.dialogue_overlaps
    assert len(d) == 1 and d[0].size_s == 0.4 and d[0].a_id.endswith("B022") and d[0].b_id.endswith("B023")
    assert ep.audit.blocks_out_of_start_order == 0


def test_dialogue_crossing_shot_boundaries(ep):
    c = dialogue_crossings(ep)
    assert c["dialogue_lines"] == 17 and c["lines_crossing_shot"] == 4 and c["groups_crossing_shot"] == 4
    assert c["max_boundaries_in_one_line"] == 1
    assert sorted(c["crossing_line_durations_s"]) == [1.3, 2.4, 2.5, 3.3]   # lines B020, B023, B034, B008


def test_line_ending_exactly_on_a_boundary_does_not_cross(ep):
    # B006 ends at 9.0 where a shot also ends: not "inside"
    from promofinder.timeline import boundaries_inside, shot_boundaries
    assert boundaries_inside(7.5, 9.0, shot_boundaries(ep.blocks)) == []


# ---- audit metrics ---------------------------------------------------------------------------------
def test_first_times(ep):
    assert first_times(ep) == {"first_spoken_s": 7.5, "first_noncamera_s": 0.5}


def test_cliffhanger_mentions_and_location(ep):
    m = cliffhanger_mentions(ep)
    assert len(m) == 1 and m[0]["where"] == "sfx" and m[0]["in_ending"] is True and m[0]["offset_from_end_s"] == 1.0


def test_ending_signals(raw, ep):
    s = ending_signals(ep)
    assert s["last_block_kind"] == "camera" and s["last_noncamera_kind"] == "sfx"
    assert s["end_cliffhanger_word"] and s["end_cut_to_black"] and s["end_last_line_question"]
    assert s["ending_type"] == "self_labeled_cliffhanger"
    e2 = parse_text(raw.replace("cliffhanger", "sting"), SAMPLE.name)
    assert ending_signals(e2)["ending_type"] == "ends_on_question"
    e3 = parse_text(raw.replace("cliffhanger", "sting").replace("Who else knows?", "Nobody knows."), SAMPLE.name)
    assert ending_signals(e3)["ending_type"] == "cut_to_black"


def test_token_estimates_are_positive_and_ordered(raw, ep):
    t = estimate_tokens(raw, ep)
    assert 0 < t["est_tokens_compact_chars4"] < t["est_tokens_file_chars4"]
    assert t["est_tokens_file_words"] > 0


def test_duplicate_detection(ep):
    assert normalize_for_dup("Don't GO...!") == "don t go"
    same = dup_pairs(ep.units, ep.units)
    assert same and all(p["ratio"] >= 0.8 for p in same)
    assert any(p["exact"] for p in same)
    assert all(len(normalize_for_dup(p["cur_text"]).split()) >= 4 for p in same)
    assert dup_pairs([], ep.units) == []


# ---- end to end -------------------------------------------------------------------------------------
def test_run_audit_on_sample_writes_outputs(tmp_path):
    rc = run_audit(str(SAMPLE), str(tmp_path))
    assert rc == 0
    for name in ("audit_report.txt", "episodes.csv", "glue_pairs.csv", "camera_gaps.csv", "cliffhanger_mentions.csv"):
        assert (tmp_path / name).exists()
    assert "ALL GLUED PAIRS" in (tmp_path / "audit_report.txt").read_text(encoding="utf-8")


def test_run_audit_reports_failures_loudly(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "good.txt").write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "in" / "bad.txt").write_text("not an episode", encoding="utf-8")
    assert run_audit(str(tmp_path / "in"), str(tmp_path / "out")) == 1
    assert "FAILED TO PARSE: bad.txt" in (tmp_path / "out" / "audit_report.txt").read_text(encoding="utf-8")
