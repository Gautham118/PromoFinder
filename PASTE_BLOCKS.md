# Paste blocks (docs). Nothing here contains pack content.

## 1. CLAUDE.md section 3: tick Stage 5 code (replace the Stage 5 line)
  - [~] 5. Rank and report, one-command CLI (code done and tested offline; real-pack run pending)

## 2. CLAUDE.md section 7: replace the "Hook window" row with
| Hook window | Record offset of the first SPOKEN line (first utterance) in the clip from the clip start (D3) | Set `late_hook` flag if > 3.0s; the hook score gate decides, no hard reject |

## 3. CLAUDE.md section 7: replace the "Overlap in one episode" row with
| Overlap in one episode | Overlap longer than 30% of the SHORTER clip with a higher-ranked pick (D24) | Drop |

## 4. CLAUDE.md section 15 (Decisions log): append
- Stage 5 built (decision.md D22-D29): `promofinder run` wires Stages 2-4, gate, weighted rank, overlap/cap/recap rules, JSON/CSV/HTML, separate failure log. Default provider is now groq with openai/gpt-oss-120b; mock must be requested with `--provider mock` and is labelled in console, HTML, manifest and MOCK_RUN.txt. Judge errors are logged per episode, never crash the run. Real model quality is still unverified until a run on the pack.
- Cut order applied against the 8pm deadline (D29): video, random baseline, consistency runs cut; grading drops from 8 to 5 episodes.
- Section 7 hook-window row fixed to match D3 (first spoken line).

## 5. decision.md: append (and change "Last updated" to 2026-11-09)

## Stage 5

**D22. Model: `openai/gpt-oss-120b` on Groq (config.yaml default, provider groq).**
- Why: you picked it in the model list; it is the largest open-weight instruction model offered there (D5 rule: weaker models fail strict JSON and unit IDs first). Fallback if it fails JSON on the 3-episode dev run: `openai/gpt-oss-20b`.
- Risk (unverified): Groq free-tier token-per-minute and per-day limits. The client already waits on Retry-After, and a re-run reuses the cache, so a daily cap only delays the run. Check your limits on the Groq console before the full run.
- Reversible: yes (config.yaml or `--model`). Status: in force.

**D23. Real vs mock cannot be confused.** config.yaml now defaults to groq. `--provider mock` prints a banner, writes `MOCK_RUN.txt`, puts a red banner in report.html, sets `"is_mock": true` and a `mock-` run id. A real provider with no key exits with code 2 before anything runs.

**D24. Overlap rule: overlap longer than 30% of the SHORTER clip drops the lower-ranked pick.** CLAUDE.md said only "overlap > 30%"; this fixes the denominator. Reversible: one constant (`OVERLAP_LIMIT`).

**D25. Ranking order:** recap-flagged picks sort after unflagged ones (demoted, still listed); then weighted score; then density; then earlier start. Max 2 per episode, global top 15. The `spoiler_risk` tie-break is not built (no flag exists yet). The "between promos" duplicate check is not built; only the check against the previous episode is.

**D26. Failure handling.** Per episode: propose errors, judge errors (wrong count, evidence outside the clip, invalid JSON after the repair retry) and any unexpected exception become rows in `failures.json/csv` and the run continues. Validator rejections go in the same file (kind `rejected_candidate`) so validity rate can be counted. Exit code is 1 if any non-validator failure exists. Raw per-episode outputs are written as produced in `raw/`.
- Note: a judge reply that is valid JSON but breaks the evidence rule is cached, so a plain re-run repeats it. Use `--fresh` to re-ask.

**D27. Provenance.** Every promo carries provider, model, `prompt_hash` (rubric + both prompt templates, 12 hex chars) and `run_id`. Freeze and record the hash before the held-out run (D9).

**D28. Judge candidate IDs are NOT relabelled yet.** The judge already sees a seeded shuffle, so order is blunted. Relabel only if the dev run shows the judge favouring C01. Not built.

**D29. Cut order applied for the 8pm deadline.** Cut: video, random baseline, consistency runs. Graded episodes: 5 (dev E008, E021, E033 plus held-out E048, E042, in D9 order). Kept: audit, validator, 50-episode raw outputs, B1-vs-final comparison, failure log, one-command run, 2-page doc.
- Added `--only` (for the 3-episode token measurement and the graded set) and `--fresh` (bypass cache). Both are in the run command, not new features of the plan.

## 6. README.md: replace the title line and add
# PromoFinder (Stages 1-5 implemented)
## Run (real)
```
.\.venv\Scripts\activate
python -m promofinder run --input ..\data --out ..\outputs\run1 --only E008,E021,E033
python -m promofinder run --input ..\data --out ..\outputs\full
```
Defaults come from config.yaml (groq, openai/gpt-oss-120b). Needs GROQ_API_KEY in .env. `--provider mock` runs offline and is labelled MOCK. Re-running reuses `.cache/`; `--fresh` re-asks the model.
Outputs: promos.json/csv (ranked), candidates.json/csv (every judged clip with its status), failures.json/csv, raw/<episode>.json, report.html, run_manifest.json, audit/.
