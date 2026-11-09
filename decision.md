# decision.md - decisions made on the author's behalf

How this file works: the author asked Claude to make project decisions for them and to record each one here with the reason.
Each entry: what was decided, why, whether it is easy to reverse, and its status. CLAUDE.md stays the source of truth for
the plan; plan-level changes are ALSO added to its Decisions log. Contains no pack content (numbers only).
Updated every session. Last updated: 2026-11-08 (after Stages 2-4 implementation).

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

## Next steps (the author's short list)
1. Unzip the repo, run the Codex prompt, and confirm `pytest` shows 37 passed and `git status` shows no `data/` or `outputs/`.
2. Create a free Groq key at console.groq.com, turn on Zero Data Retention in Data Controls, and confirm the no-training statement on Groq's own pages (D4). Put the key in `.env` as `GROQ_API_KEY`. Do not paste it into any chat.
3. Run the Stage 1 audit locally on the real pack (outside Codex's folder) and check it matches the numbers in CLAUDE.md.
4. Implement Stage 5: wire Stages 2-4 into the resumable `run` CLI, then add deterministic gating, ranking, deduplication, and JSON/CSV/HTML reports.
