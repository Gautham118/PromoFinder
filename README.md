# PromoFinder (Stage 1: data audit and parser)

CLI tool for Kuku's AI Generalist Assignment, Problem 1: find and rank the best 20-60s promos in microdrama screenplay files.
Only Stage 1 exists so far: parser, utterance-group layer and data audit. No LLM code yet. See `CLAUDE.md` for the plan.

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

## Confidentiality
The pack is confidential. `data/`, `outputs/`, `.env` and `.cache/` are ignored by git. `sample/` is an invented episode.
