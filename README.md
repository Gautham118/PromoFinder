# PromoFinder (Stages 1-5 implemented)

CLI tool for Kuku's AI Generalist Assignment, Problem 1: find and rank the best 20-60s promos in microdrama screenplay files.
Implemented: parser/audit, an ID-only whole-episode proposer, deterministic validation and
boundary snapping, and a clip-only judge. Stage 5 will expose them through the resumable
one-command `run` CLI and add ranking/reporting. See `CLAUDE.md` for the plan.

The LLM layer is deliberately swappable: `mock` is offline, while the OpenAI-compatible
client supports Groq and similar APIs. `config.yaml` contains non-secret defaults; credentials
are read from the configured environment variable and `.cache/` stores resumable responses.

## Setup
```
python -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -r requirements.txt
```

## Run the audit
Keep the confidential pack and the audit outputs OUTSIDE the repo, next to it:
```
Kuku Assignment/
  data/      <- unzipped episode .txt files (any subfolder depth)
  outputs/   <- audit results (contain episode text)
  repo/      <- this repo (the only git repo)
```
From inside `repo/` (PowerShell):
```
.\.venv\Scripts\activate
python -m promofinder audit --input ..\data --out ..\outputs\audit
```
Exit code 1 means at least one file failed to parse; it is listed at the top of the report.
`data/` and `outputs/` inside the repo are also git-ignored as a second guard.

## Tests (synthetic sample only)
```
python -m pytest -q
```

## Run (real)
```
.\.venv\Scripts\activate
python -m promofinder run --input ..\data --out ..\outputs\run1 --only E008,E021,E033
python -m promofinder run --input ..\data --out ..\outputs\full
```
Defaults come from config.yaml (groq, openai/gpt-oss-120b). Needs GROQ_API_KEY in .env. `--provider mock` runs offline and is labelled MOCK. Re-running reuses `.cache/`; `--fresh` re-asks the model.
Outputs: promos.json/csv (ranked), candidates.json/csv (every judged clip with its status), failures.json/csv, raw/<episode>.json, report.html, run_manifest.json, audit/.

## Confidentiality
The pack is confidential. `data/`, `outputs/`, `.env` and `.cache/` are ignored by git. `sample/` is an invented episode.
