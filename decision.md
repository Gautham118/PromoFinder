# decision.md - decisions made on the author's behalf

How this file works: the author asked Claude to make project decisions for them and to record each one here with the reason.
Each entry: what was decided, why, whether it is easy to reverse, and its status. CLAUDE.md stays the source of truth for
the plan; plan-level changes are ALSO added to its Decisions log. Contains no pack content (numbers only).
Updated every session. Last updated: 2026-11-09 (after Stages 2-4 implementation).

## Working rule (D0)
- **Decision:** Claude decides by default, states one clear choice, logs it here, and flags any conflict with CLAUDE.md. It only asks the author for something Claude cannot do (create keys, run things locally, spend money).
- **Why:** the author asked for this, and Kuku takes no questions, so every ambiguity needs a stated default anyway.
- **Reversible:** yes, at any time. Say "reverse D#".

## Stage 1 closeout

**D1. Keep the glue rule exactly as written (same speaker name, next dialogue line, gap <= 1.5s).**
- Why: measured on all 50 episodes: 38 glued pairs with gaps from -0.6s to 1.1s (median 0.2s), 1 rejected near-miss, 0 orphan "..." openers. No evidence the threshold is wrong, and the 52 trailing-off ellipses are correctly left unglued.
- Spot-check is reduced to an OPTIONAL 10 pairs (not 20). The one pair with a negative gap (E003, B002 to B004) is the only one worth a look.
- Reversible: yes (one constant, `MAX_GLUE_GAP_S`). Status: done.

**D2. Skip the hand-check of continuity notes. Do not feed continuity notes to any LLM stage.**
- Why: the proposer only needs the logline (CLAUDE.md section 5), the judge sees clip text only, and 12 of the 50 notes mention the word "cliffhanger", which is a leak risk for no gain. Verifying the claim by hand costs time and changes nothing we build.
- Reversible: yes. Status: decided.

**D3. Change the hook-window flag: measure `late_hook` from the first SPOKEN line (first utterance) in the clip, flag if more than 3.0s. Still flag-only, never a hard reject.**
- Why: the original rule used the first non-CAMERA event, but that event is at 0.0s in 32 of 50 episodes and never later than 2.9s, so the rule would almost never fire and would tell us nothing. The first spoken line (median 5.0s, max 22.0s at episode start) actually varies.
- Conflict with CLAUDE.md section 7 (hook window row): this supersedes that row; logged in the CLAUDE.md Decisions log. The LLM hook score still decides post-worthiness.
- Reversible: yes (Stage 3 validator only). Status: implemented.

## Provider and confidentiality

**D4. Never send pack content to Google AI Studio / Gemini free tier. Use Groq (free tier) for every run that touches pack text.**
- Why: Gemini's unpaid quota terms say Google may use submitted content to improve its products and that human reviewers may read it. That is unacceptable for Kuku's confidential episodes. Groq's free tier is documented (services agreement, as summarised by third-party sources) as not permitted to train on inputs, with no default retention of inference data and a self-serve Zero Data Retention setting. This also keeps CLAUDE.md's "free tiers first" rule intact.
- Caveat: those Groq statements come from secondary summaries. Before the first real run the author should confirm them on Groq's own data-controls page and turn ZDR on (see the action list).
- Gemini and any other provider may still be used on the SYNTHETIC sample only.
- Reversible: yes, but never toward a training-on-inputs tier for pack text. Status: decided.

**D5. Build the OpenAI-compatible adapter first (Groq speaks it), plus the `mock` provider. Skip the Google adapter unless time is left.**
- Why: CLAUDE.md section 13 already says to keep the OpenAI-compatible adapter and cut the second one. One adapter also covers OpenRouter and probably Kuku's own API.
- Model name, base URL and key variable live in `config.yaml` and `.env`, never in code (CLAUDE.md section 14). The exact Groq model is chosen when the key is created, from what the free tier offers that day; prefer the largest open-weight instruction model available, because weaker models will fail the strict JSON and unit-ID outputs first.
- Reversible: yes. Status: implemented in Stage 2.

## Design

**D6. One whole-episode call per episode for the proposer; no chunking.**
- Why: episodes are small (about 1.0k to 4.0k tokens by estimate, median about 2.6k), so they fit in one call with room to spare, and a single view of the episode is what the proposer needs.
- Reversible: yes. Status: decided.

**D7. Leave the self-labeled "cliffhanger" wording in the text the models see. Report it as a known leak and a free weak signal instead of stripping it.**
- Why: stripping would change source text, break exact-quote verification, and would not match what Kuku's tool sees on a new show. It appears in only 14 of 50 episodes, always in the last 10s. We will report in the doc how many final picks contain it.
- Reversible: yes (would be a pre-processing step). Status: decided.

**D8. Keep the rubric weights (0.3 / 0.3 / 0.2 / 0.2) and the gate (min of hook, cliffhanger, standalone >= 2) as written, and keep calling them unvalidated.**
- Why: no data yet to justify changing them; changing numbers without evidence is the thing the doc is judged against.
- Reversible: yes. Status: unchanged.

## Evaluation

**D9. Fixed-seed episode split (seed 2026, first draw, no cherry-picking). Dev: E008, E021, E033. Held-out: E048, E042, E007, E015, E039.**
- Why: CLAUDE.md requires the split to be fixed by seed before any output is seen; this draw was made before the proposer exists. It happens to include a short episode (E042) and the one with the longest silent opening (E007), so it is not degenerate. Prompts are frozen (prompt hash recorded) before the held-out five are run.
- Reversible: only before any proposer output is seen. After that it is locked. Status: locked as of this entry.

**D10. Plan for 8 graded episodes, with one checkpoint: if the prototype (Stages 2 to 5) is not running end to end by the time Phase 1's 2.5 hours are used, drop grading to 5 episodes (3 dev + 2 of the held-out, taken in the order listed in D9).**
- Why: CLAUDE.md's cut order already puts "grading from 8 to 5" after video, random baseline and consistency runs. Fixing the rule now means no decision is needed while tired.
- Reversible: yes. Status: decided.

**D11. Deliver changes only, not whole files or zips, from now on.**
- Why: the author already keeps a working repo; re-sending everything wastes time and risks overwriting local edits, including Codex's.
- Reversible: yes, ask for a full zip any time. Status: in force.

**D12. Repo layout: repo/ is the only git repo. data/ and outputs/ stay OUTSIDE it, in the parent folder.**
- Why: the confidential pack and audit outputs can never be committed by accident if they are not inside a repo. .gitignore stays as a second guard. The extra .git and stale CLAUDE.md in the parent folder are to be removed.
- Run from repo/: python -m promofinder audit --input ..\data --out ..\outputs\audit. Zips must be unzipped first.
- Reversible: yes. Status: in force.

**D13. Stage 1 accepted. Verified on the real pack by the author's own local run.**
- All audit numbers in the CLAUDE.md Decisions log were reproduced exactly.
- All 38 glued pairs were read: each is one sentence split in two. D1 is confirmed. One real continuation interrupted by another speaker's line stays unglued (1 of 39 ellipsis pairs); a contiguous clip containing both halves must contain the interrupter anyway.
- The same character appears under different labels across episodes, so no logic may use labels for identity. Glue is unaffected (0 different-speaker rejections).
- Status: Stage 1 closed.

**D14. Codex's Stage 2-4 code is the base; Claude's earlier unmerged Stage 2 is discarded.** Review found 6 defects (cut inside speech accepted, no repair retry, retry policy, cwd-dependent rubric path, judge shuffle/quote checks, undefined score-2) and no tests; all fixed in one patch with 29 new tests (67 pass).
**D15. Keep Codex's client interface (complete_json(system, user, response_model)) instead of CLAUDE.md's generate_json(messages, schema, temperature, seed).** Same function and still swappable. Recorded as a deviation.
**D16. The validator REJECTS cuts inside an utterance (never repairs them).** The section 7 "trim edge units" duration repair is not built; reject only. Revisit if real runs show many near-miss durations.
**D17. Model replies may contain extra keys (ignored). One repair retry for invalid JSON or schema, then a loud failure. A bad reply is never cached.**
**D18. rubric.md now defines the score-2 anchors (tunable); the gate depends on what 2 means.**
**D19. Judge: seeded shuffle of clip order, clips wrapped as data, quotes in reasons verified or stripped. Evidence IDs outside the clip still raise (Stage 5 must record that as a failure, not crash).**
**D20. Measured (estimates, chars/4): proposer 1.7k-4.0k input tokens per call (median 2.8k, total ~142k); judge up to ~4.9k for 5 clips.**
**D21. Stress test on all 50 real episodes with 2000 random windows: 0 crashes, 14% rejected for cutting inside speech.**
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