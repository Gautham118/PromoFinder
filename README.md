# PromoFinder (Stage 1: data audit and parser)

CLI tool for Kuku's AI Generalist Assignment, Problem 1: find and rank the best 20-60s promos in microdrama screenplay files.
Only Stage 1 exists so far: parser, utterance-group layer and data audit. No LLM code yet. See `CLAUDE.md` for the plan.

## Setup
```
python -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -r requirements.txt
```

## Run the audit
Put the episode `.txt` files in `data/` (gitignored), then:
```
python -m promofinder audit --input data/<folder> --out outputs/audit
```
Prints the report and writes `audit_report.txt` plus CSVs to `outputs/audit/` (gitignored: it contains episode text).
Exit code 1 means at least one file failed to parse; the failure is listed at the top of the report.

## Tests (synthetic sample only)
```
python -m pytest -q
```

## Confidentiality
The pack is confidential. `data/`, `outputs/`, `.env` and `.cache/` are ignored by git. `sample/` is an invented episode.
