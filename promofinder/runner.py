"""`python -m promofinder run`: audit, then propose -> validate -> judge -> rank -> report. Re-runs reuse .cache."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .audit import run_audit
from .config import load_config, load_dotenv
from .llm import create_client
from .models import Failure, ParsedEpisode
from .parser import ParseError, list_episode_files, parse_file
from .pipeline import compute_prompt_hash, recap_set, run_episodes
from .rank import rank_candidates
from .report import write_outputs


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="promofinder run", description="Find and rank 20-60s promos.")
    ap.add_argument("--input", required=True, help="directory of episode .txt files, or one file")
    ap.add_argument("--provider", help="mock | groq | openai-compatible (default: config.yaml)")
    ap.add_argument("--model", help="model name (default: config.yaml)")
    ap.add_argument("--out", default="outputs/run", help="output directory (keep it outside the repo for real runs)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--only", help="comma-separated episode ids to run, e.g. E008,E021,E033")
    ap.add_argument("--fresh", action="store_true", help="bypass the response cache (re-ask the model)")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    provider, model = a.provider or cfg.provider, a.model or cfg.model
    is_mock = provider == "mock"
    if is_mock:
        print("!" * 70 + "\n!! MOCK RUN: no model will be called. Output is NOT real. !!\n" + "!" * 70)
    else:
        load_dotenv(".env")
        if not os.environ.get(cfg.api_key_env):
            print(f"Missing API key: set {cfg.api_key_env} in .env or the environment.", file=sys.stderr)
            return 2
        print(f"REAL RUN: provider={provider} model={model}")

    out = Path(a.out)
    if run_audit(a.input, str(out / "audit")) == 2:           # audit first, as CLAUDE.md section 10 requires
        return 2
    episodes: list[ParsedEpisode] = []
    extra: list[Failure] = []
    for p in list_episode_files(a.input):
        try:
            episodes.append(parse_file(p))
        except ParseError as e:
            extra.append(Failure(episode_id=p.stem, stage="parse", kind="parse_error", detail=str(e)[:500]))
    episodes.sort(key=lambda e: e.episode_id)
    if a.only:
        want = {x.strip().upper() for x in a.only.split(",") if x.strip()}
        episodes = [e for e in episodes if e.episode_id in want]
        missing = want - {e.episode_id for e in episodes}
        if missing:
            print(f"--only ids not found: {sorted(missing)}", file=sys.stderr)
            return 2

    client = create_client(provider, model=model, base_url=cfg.base_url, api_key_env=cfg.api_key_env, cache_dir=cfg.cache_dir,
                           timeout_s=cfg.timeout_s, max_retries=cfg.max_retries)
    results = run_episodes(episodes, client, cache_bypass=a.fresh)
    entries = rank_candidates([j for r in results for j in r.judged], recap_set(episodes, results))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    meta = {"run_id": f"{'mock' if is_mock else 'run'}-{stamp}", "provider": provider, "model": model, "is_mock": is_mock,
            "prompt_hash": compute_prompt_hash(), "episodes": len(episodes), "cache_bypassed": a.fresh,
            "usage": getattr(client, "usage", None)}
    failures = write_outputs(out, entries, results, extra, meta)
    hard = [f for f in failures if f.stage != "validate"]
    print(f"\nrun {meta['run_id']}: {sum(e.status == 'ranked' for e in entries)} ranked promos from {len(episodes)} episodes; "
          f"{len(hard)} episode failures; {len(failures) - len(hard)} rejected candidates. Wrote {out}/")
    return 1 if hard else 0
