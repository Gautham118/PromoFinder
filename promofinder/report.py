"""Stage 5c: write JSON, CSV and HTML. Raw per-episode outputs are dumped as produced; failures go to their own file."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from .models import EpisodeResult, Failure, Promo, QuotedUnit, RankedEntry, Segment


def mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


def to_promo(entry: RankedEntry, meta: dict[str, Any]) -> Promo:
    j = entry.judged
    c = j.candidate
    flags = list(c.flags) + (["recap_overlap"] if entry.recap else [])
    ep_id = c.candidate_id.rsplit("-C", 1)[0]
    return Promo(
        promo_id=c.candidate_id, episode_id=ep_id, rank=entry.rank, status=entry.status, gate_passed=entry.status != "below_gate",
        score_breakdown={**j.scores.model_dump(), "weighted": entry.weighted},
        segments=[Segment(episode_id=ep_id, start_s=c.start_s, end_s=c.end_s, start_unit=c.start_id, end_unit=c.end_id)],
        duration_total_s=c.duration_s, hook=QuotedUnit(unit_id=c.hook_id, text=c.hook_quote),
        cliffhanger=QuotedUnit(unit_id=c.cliffhanger_id, text=c.cliffhanger_quote), reason=j.reason, flags=flags,
        provider=meta["provider"], model=meta["model"], prompt_hash=meta["prompt_hash"], run_id=meta["run_id"])


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _flat(p: Promo) -> dict:
    s = p.segments[0]
    return {"promo_id": p.promo_id, "episode_id": p.episode_id, "rank": p.rank, "status": p.status, "start_s": s.start_s, "end_s": s.end_s,
            "start": mmss(s.start_s), "end": mmss(s.end_s), "duration_s": p.duration_total_s, **{f"score_{k}": v for k, v in p.score_breakdown.items()},
            "flags": ";".join(p.flags), "hook_unit": p.hook.unit_id, "hook": p.hook.text, "cliffhanger_unit": p.cliffhanger.unit_id,
            "cliffhanger": p.cliffhanger.text, "reason": p.reason, "model": p.model, "prompt_hash": p.prompt_hash, "run_id": p.run_id}


def render_html(promos: list[Promo], failures: list[Failure], meta: dict[str, Any]) -> str:
    e = html.escape
    banner = ('<p style="background:#fdd;border:2px solid #c00;padding:8px"><b>MOCK RUN: no model was called. Nothing below is real output.</b></p>'
              if meta["is_mock"] else "")
    top = [p for p in promos if p.status == "ranked"]
    rows = "".join(
        f"<tr><td>{p.rank}</td><td>{e(p.episode_id)}</td><td>{mmss(p.segments[0].start_s)}-{mmss(p.segments[0].end_s)} ({p.duration_total_s:.1f}s)</td>"
        f"<td>{p.score_breakdown['weighted']}</td><td>{e(p.hook.text)}</td><td>{e(p.cliffhanger.text)}</td><td>{e(p.reason)}</td><td>{e(', '.join(p.flags))}</td></tr>"
        for p in top)
    frows = "".join(f"<tr><td>{e(f.episode_id)}</td><td>{e(f.stage)}</td><td>{e(f.kind)}</td><td>{e(f.detail)}</td></tr>" for f in failures)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>PromoFinder {e(meta['run_id'])}</title>
<style>body{{font-family:sans-serif;margin:24px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 8px;vertical-align:top}}</style></head><body>
{banner}<h1>PromoFinder report</h1>
<p>run <b>{e(meta['run_id'])}</b> | provider {e(meta['provider'])} | model {e(meta['model'])} | prompt hash {e(meta['prompt_hash'])} | episodes {meta['episodes']}</p>
<h2>Ranked promos (post first = rank 1)</h2>
<table><tr><th>#</th><th>Episode</th><th>Clip</th><th>Score</th><th>Hook</th><th>Cliffhanger</th><th>Why</th><th>Flags</th></tr>{rows}</table>
<h2>Failures and rejected candidates (separate log; raw outputs are not edited)</h2>
<table><tr><th>Episode</th><th>Stage</th><th>Kind</th><th>Detail</th></tr>{frows}</table>
</body></html>"""


def write_outputs(out: Path, entries: list[RankedEntry], results: list[EpisodeResult], extra_failures: list[Failure],
                  meta: dict[str, Any]) -> list[Failure]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw").mkdir(exist_ok=True)
    for r in results:
        (out / "raw" / f"{r.episode_id}.json").write_text(r.model_dump_json(indent=2), encoding="utf-8")
    promos = [to_promo(en, meta) for en in entries]
    ranked = [p for p in promos if p.status == "ranked"]
    failures = extra_failures + [f for r in results for f in r.failures]
    (out / "promos.json").write_text(json.dumps([p.model_dump() for p in ranked], indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "candidates.json").write_text(json.dumps([p.model_dump() for p in promos], indent=2, ensure_ascii=False), encoding="utf-8")
    _csv(out / "promos.csv", [_flat(p) for p in ranked])
    _csv(out / "candidates.csv", [_flat(p) for p in promos])
    (out / "failures.json").write_text(json.dumps([f.model_dump() for f in failures], indent=2, ensure_ascii=False), encoding="utf-8")
    _csv(out / "failures.csv", [f.model_dump() for f in failures])
    (out / "report.html").write_text(render_html(promos, failures, meta), encoding="utf-8")
    meta = {**meta, "counts": {"ranked": len(ranked), "candidates_judged": len(promos), "failures": len(failures),
                               "by_status": {s: sum(p.status == s for p in promos) for s in sorted({p.status for p in promos})}}}
    (out / "run_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if meta["is_mock"]:
        (out / "MOCK_RUN.txt").write_text("This folder came from the mock provider. No model was called.\n", encoding="utf-8")
    return failures
