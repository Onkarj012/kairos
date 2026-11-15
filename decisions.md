# Kairos (aka Meridian; includes ~/Projects/market/meridian) decisions

Log of project decisions, newest first. Kept by Ledger from T3 Code threads.

Entry format:

## YYYY-MM-DD — decision in one line
- rejected: ...
- why: ... (or "not stated")
- source: T3 thread "<title>"
- status: active | superseded by <date — decision>

---

## 2026-11-15 — Scheduler entry points share one home-state lock and reject unsupported checkout paths before installation
- rejected: XDG-dependent fallback locks, a separate service override, and silently substituting arbitrary paths into systemd units
- why: Greptile found the CLI and services could hold different locks, and spaces/replacement/specifier characters could corrupt installed services. Use the stable user home-state lock regardless of log-state or XDG runtime settings; any explicit override must match everywhere. Reject checkout paths outside the documented safe ASCII set before writing units rather than introducing multiple escaping layers. This supersedes the earlier runtime/private-state fallback choice, not the owner-only activation boundary.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; PR 14 Greptile review of c10cefc
- status: active

## 2026-11-15 — Ticket 14 adds owner-activated user units and shared locks, not an installed working daily pipeline
- rejected: automatic unit activation, independent per-job writer locks, start-time success heartbeats or successful no-op job adapters
- why: new systemd unit files are preparation only; the owner must review before installation/activation. All three jobs share one nonblocking lock, and each completed job has its own heartbeat. Missing handlers/token/notifier fail closed rather than fabricate picks or completed work. The external monitor and real reminder/data/model/report adapters are not configured or proven; ticket 14 and its dependent paper gate remain incomplete. Standalone lock defaults to user runtime/private state, not a system lock directory.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; ticket 14 implementation `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket14-20261115-01`
- status: active for unit/adaptor/monitor boundaries; runtime-dependent lock fallback superseded by 2026-11-15 scheduler home-state lock decision above

Review clarification, 2026-11-15 IST: heartbeat publication follows completion
logging as the terminal success operation; non-object heartbeat JSON is invalid.
Uninstall stops every timer before stopping services and removes files only if
all manager commands succeed. Rejected swallowing manager errors or refreshing
success before a fallible log write because either hides failure. Source: ticket
14 independent round-one review in this coordinator thread. Status: active.

## 2026-11-15 — Ticket 02 retains login in a minimal helper rather than retaining broker code for token utilities
- rejected: keeping the old Kite broker and its order API solely to supply login constants and token persistence
- why: preserve the standing one-tap login path, token JSON/private permissions and IST behavior without retaining the dropped runner or order machinery. Login checks use injected settings and temporary tokens only, not a real callback, account or network call.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; ticket 02 implementation `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket02-20261115-01`
- status: active

## 2026-11-15 — Serialize cleanup tickets 02 then 03 while independent source-port work continues
- rejected: simultaneous deletion branches for runner/rules and research code with mixed callers/tests
- why: ticket 01's merge unlocks both tickets, but old research backtest imports daily_rules and mixed tests cross their deletion scopes. Serial integration avoids broken intermediate caller assumptions without changing canonical prerequisites or delaying independent ticket 08 work.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; static import inspection at merged main b993634 on 2026-11-15 IST
- status: active

## 2026-11-15 — Ticket 08 preserves exported V3 source with isolated imports and minimal package initializers
- rejected: reconstructing the architecture from a paper summary, executing unseen Mac package startup code, or importing the real training script
- why: exact hash-verified source is available, including the original model's ResidualBlock and RegimeDetector dependency. Preserve computational classes, normalize whitespace, redirect three absolute imports into `models.stockxpert_port`, and use docstring-only package initializers. Source and local-adaptation hashes plus the dirty training-script caveat are recorded in provenance.md. Only synthetic CPU forward/backward and target arithmetic are exercised. The full feature builder still needs unexported indicator/event-calendar modules; the scaler needs scikit-learn and is not runtime-tested. These limitations must not be reported as a verified training pipeline or model performance.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; ticket 08 implementation `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket08-20261115-01`
- status: active

## 2026-11-15 — Kept normalized calendars infer only omitted weekends inside their represented date range
- rejected: rejecting every omitted weekend, inferring missing weekday sessions, or filling dates outside retained coverage
- why: PR 11 Greptile review showed the existing normalized producer omits ordinary weekends, so explicit-date-only lookup incorrectly closed Friday approvals before reaching Monday. Keep the normalized sessions/manifest format and independence from retired proof modules, fill omitted in-range weekends as non-sessions, preserve explicit special weekend rows, and refuse missing weekdays or out-of-range dates. Calendar provisioning remains separate and unverified.
- source: PR 11 review https://github.com/Onkarj012/kairos/pull/11#discussion_r4578749239; coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`
- status: active

## 2026-11-15 — Kept paper-calendar loading uses normalized session records, not the retired proof chain
- rejected: importing old calendar-proof modules into kept dashboard code, requiring frozen runner-ledger artifacts, or guessing weekday sessions when calendar evidence is missing
- why: independent ticket 01 review found those imports would break on ticket 03's authorized deletions. Read the existing `meridian.nse-calendar.public-v3` sessions/manifest format under `<derived-root>/calendar`, verify its count and hash, and use only explicitly listed session dates. Missing or invalid records close approvals without preventing read-only history. Existing retained artifacts are not converted here; approval timing needs this normalized artifact provisioned and validated separately. Restore existing-answer no-write retries before dashboard appends so cross-channel and opposite-answer retries preserve account/log behavior.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; review `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket01-review-20261115-r1`
- status: superseded by 2026-11-15 — Kept normalized calendars infer only omitted weekends inside their represented date range

## 2026-11-15 — Ticket 01 extracts neutral account/log/calendar modules and retains legacy compatibility callers
- rejected: deleting legacy files in ticket 01, changing paper-log formats or forcing the dashboard through the old runner
- why: preserve existing account folding and hash-chain history while making kept account/dashboard/API code independent of old strategy, sealed-test and runner imports. Legacy modules remain compatibility callers until tickets 02/03 remove them under their own scopes. Coordinator validation may read the existing absolute root virtual environment's installed dependencies without modifying that environment or the owner checkout.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; ticket 01 worker `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket01-20261115-01`
- status: active

## 2026-11-15 — Live daily inputs exclude the morning request's session and require flat scalars; verified reads consume private snapshots
- rejected: inclusive same-day daily closes, nested feature containers carrying hidden answers, or verifying one file version and yielding another
- why: independent ticket 04 review reproduced all three leaks, a publication collision failure and missing first-audit directory durability. Use completed sessions strictly before the request date and a flat scalar schema; consume the same verified bytes with an 8 MiB memory spool threshold and explicit temporary-disk capacity needs. Identical publication collisions verify the winner; owner authorization requires a pre-provisioned durable audit directory and file-plus-directory sync. These fixes do not establish OS isolation, real migration evidence or actual crash recovery.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; independent review `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket04-review-20261115-r1`
- status: active

## 2026-11-15 — Ticket 04 separates pack/reader infrastructure from real migration and enforced owner-service protection
- rejected: declaring ticket 04 complete from synthetic tests, using a Python password/private name as vault security, or treating uncompressed JSONL as proven compact minute storage
- why: the initial implementation uses standard-library JSONL packs with checksummed manifests and per-session live packs, avoiding new dependencies while checking date refusal and bounded input projection. These are provisional infrastructure choices, not accepted 439-million-row storage or migration evidence. A dedicated owner-service OS identity and authenticated IPC remain necessary; no such service is deployed. Real partition counts/checksums and enforced filesystem separation must be accepted before 05/17 unlock. Ticket 17 must demonstrate compact source-preserving packing within storage limits, not blindly expand minute data into JSONL.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; ticket 04 worker `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket04-20261115-01`
- status: active

## 2026-11-15 — Ticket 08 uses an isolated CPU-only check environment and complete hash-verified Mac source, not a summary reconstruction
- rejected: reconstructing the published model from a helper summary, treating missing dependencies as a completed model check, installing a GPU stack or modifying shared project environments
- why: the first source helper returned a partial summary and the worker's interpreter lacked PyTorch. Resume the same worker using complete existing-helper source output or a follow-up to that helper. Permit a private temporary CPU-only runtime after resolution and size checks, capped at 2 GB new disk use while leaving at least 8 GB free. No real-data training or Mac writes are authorized; source fidelity and successful dummy forward/backward evidence are still required.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; ticket 08 worker `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-ticket08-20261115-01`
- status: active

## 2026-11-15 — Daily bars with unknown target/stop order use masked training answers and conservative stop-first replay
- rejected: guessing intraday order from the close or candle direction, training on guessed answers, or reporting target-first gains without event-order evidence
- why: Greptile's setup PR review identified that a daily bar opening between target and stop can touch both without showing which came first. Tickets 05 and 12 share one ambiguity classification. Keep other valid training answers, count and mask the ambiguous target-before-stop answer, and settle an ambiguous open position stop-first in replay. Observed opening gaps through a stop use the opening price with costs/slippage. Entry-fill eligibility remains independent. Later minute data may resolve order only within the existing date and information restrictions.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; PR review https://github.com/Onkarj012/kairos/pull/9#discussion_r4578505419
- status: active

## 2026-11-15 — Calibration and replay producers must pass before dependent model work; setup restrictions do not ban authorized later tickets
- rejected: starting 09 without 07 calibration/table-model evidence, 26 without 12 swing replay, or 28 without 21 same-day replay; making setup-only training and Mac restrictions permanent
- why: independent pre-PR review found these missing prerequisites and conflicting safety instructions. Add the three edges to tickets and execution ledger. Permit only audited explicit synthetic tests during cleanup, including startup and plugin audits. Later ticket-scoped pre-2025 training, read-only Mac source transfer for 08, and source-preserving output packing for 17 remain authorized. No agent opens the vault or scores held-out dates; live broker/account calls and deployment remain unauthorized. Restore the data-symlink ignore rule and owner-only resume permission.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; independent review `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/thread:delegated-task:command%3Amcp%3A8d28fe84-fecd-4bcf-a8ca-67a3424b45b5%3Adelegate-task%3Akairos-ai-setup-review-20261115-r1`
- status: active

## 2026-11-15 — Kairos AI execution is owner-authorized under one-coordinator, bounded-worker orchestration
- rejected: the older Haiku-only high implementation policy and unconstrained parallel work across shared project state
- why: use Luna medium by default and high for judgment; Haiku may implement bounded tickets. One coordinator owns one ticket/worktree/branch/PR per change, with at most three implementation workers initially. Dispatch only disjoint scopes after checking actual overlap. An independent gpt-6.1-sol high review must review the current head before any PR. The coordinator babysits and merges only passing, authorized changes in this repository. Only the coordinator edits the shared execution ledger. This authority covers commits, pushes, PRs, in-scope fixes, and merges; it does not authorize trading, deployment, opening the vault, broker/account access, or other owner-only actions.
- source: coordinator thread `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:launch:d78501ea892b7b4e6660f492ed746eb687fdee6763e536aee24baa0788e6ff80`; owner discussion `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:a0c44843-c8c3-4603-b889-fa13467b0007`
- status: active

## 2026-11-15 — Kairos AI ticket gates preserve minute-ripple, conditional-news, and per-style promotion evidence
- rejected: freezing before the minute ripple verdict; requiring news coverage globally; promoting from shared or code-only paper evidence
- why: ticket 29 includes both daily and minute ripple verdicts; ticket 26 enables news only after ticket 25 coverage is accepted and otherwise proceeds without news; ticket 33 needs common tickets 30/31/32 plus the matching style's own accepted paper record (swing 16 or same-day 22). Ticket 22 requires its own 3–4-week shakedown and about 100 completed trades with its own passed scorecard. Stage 3 remains owner-only and requires its own blind pass.
- source: owner discussion `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:a0c44843-c8c3-4603-b889-fa13467b0007`
- status: active

## 2026-11-15 — Kairos AI execution, risk, and operations retain only adopted Kairos v2 sections 4.4–4.6
- rejected: carrying forward the superseded target-portfolio/default-ETF mandate or treating the entire Kairos v2 plan as current
- why: Kairos AI replaces strategy outputs with daily picks and replaces the small-account ETF default with fewer positions under the same picks rules. The adopted order-state/reconciliation, order-boundary risk, and private Linux operations requirements remain summarized in `.scratch/kairos-ai/retained-system-requirements.md`, subordinate to Kairos AI and this log.
- source: owner discussion `t3-thread://v2/1585e0e6-cd5d-4301-ae07-5248fda631d2/mcp:a0c44843-c8c3-4603-b889-fa13467b0007`
- status: active

## 2026-11-15 — The blind vault holds 2025-01-01 up to the day it is built; the daily picks job may read recent prices only to build inputs, and paper results count as the live record, not blind evidence
- rejected: keeping all of 2025 and 2026 sealed, including from live use (paper trading could not build its morning rows)
- why: paper trading in late 2026 needs the last 60 sessions of prices; no score is ever computed on vault dates outside the one blind run (ticket 30)
- source: T3 thread "Kairos AI tickets (to-tickets)"
- status: active

## 2026-11-15 — The small-account ETF plan is dropped; small accounts hold fewer positions under the same picks rules
- rejected: kairos-v2 ticket 06 (choose ETFs or stocks by capital, default to holding the benchmark ETF)
- why: the Kairos AI spec handles account size by the number of positions; Onkar agreed
- source: T3 thread "Kairos AI tickets (to-tickets)"
- status: active

## 2026-11-15 — Loss budget: 10% fall from the account's highest value is the hard stop; 5% halves position sizes; 2% lost in one day stops new trades for that day
- rejected: a budget sized to the old momentum book (the broad market itself fell 40% in 2020, so a 10% stop would have fired constantly on that book); no number until more research
- why: Onkar asked for 10% or the most appropriate number. A 10% stop fits a system that risks 1% of the account per trade (about ten losing trades in a row from the top). It is a review line, not a failure: after a stop the system reverts to paper until Onkar reviews and restarts it. 5% and 2% are the earlier warning steps. Numbers can be revised once paper results show how large normal swings are
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-15 — Old code may be purged; the rebuild is done cleanly instead of working around the rule-based system
- rejected: keeping every old module and ticket for compatibility
- why: Onkar is open to purging old code. Keep only what is proven useful and tested (daily price panel build, corporate-action adjustment, cost model, hash-chained event log, paper accounts); remove or replace the rest as tickets reach it. Nothing is deleted without a ticket naming it
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Two trading styles are both built: same-day (intraday) with buy and sell signals, and multi-day (swing) with buy signals only; each has its own picks list and paper record
- rejected: letting the replay test pick a single style
- why: Onkar is fine with both and defined them this way. Swing is built first because it runs on the daily prices already on hand; intraday needs minute prices
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Paying for the Zerodha data subscription and occasional graphics card rental is approved
- rejected: free data and local hardware only
- why: Onkar agreed; minute prices and the larger models need them
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Build two things on one base: a deep learning daily picks system and the ripple network research model; the ripple model becomes the picks brain only if it wins the blind test
- rejected: a rules-only system with no learned model (what the last five or six attempts ended up as); holding a momentum book for months as the main product
- why: Onkar's goal from the start was an AI model that predicts stocks, building on his published five-encoder model, plus his ripple idea. He accepts the idea may fail. Plan: `.scratch/kairos-ai/spec.md`
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Picks are published before 09:00 IST with buy range, quantity, target, stop-loss and expected holding time; held stocks get hold or sell with a range
- rejected: publishing only a target portfolio
- why: Onkar described this as the output he wants to trade from
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — 2025 and 2026 are both blind test years, kept in a file the training code cannot open; models train on data up to the end of 2024
- rejected: the single sealed year 2025-10-01 to 2026-09-30
- why: Onkar asked for at least one blind year and preferably two. The old rules were already tested through 2025-09, which must not shape the new models
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Data checks are cut to three counts; gaps in optional inputs are marked as missing and never stop model training
- rejected: the Meridian gate chain (full calendar evidence, 98% coverage thresholds, rung prerequisites before any model)
- why: those gates stopped every earlier attempt before a model was trained; `stockxpert_v3.py` is still a placeholder. Only wrong answers in the training rows or a leak from the blind years stop work
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Kairos v2: strategies only output a target portfolio; one machine turns it into reconciled broker holdings, and its default policy is holding the benchmark ETF
- rejected: patching the current runner issue by issue; building the live path around the two current rules
- why: Onkar asked for the complete system, not a fastest path. Execution, risk and ops then do not depend on which strategy wins. Plan: `.scratch/kairos-v2/spec.md`
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: partially superseded by 2026-11-15 — Kairos AI execution, risk, and operations retain only adopted Kairos v2 sections 4.4–4.6 (the target-portfolio/default-ETF mandate is superseded; adopted execution, risk, and operations requirements remain active)

## 2026-11-14 — The system runs every day and trades only when a trade pays for itself; the 3-session delivery book and intraday are dropped
- rejected: daily turnover (5-day reversal and 12-1 momentum on a 3-session hold); intraday MIS; option selling; stock futures
- why: both rules failed every gate; costs were 42 to 53 bps a trade against about 37 to 47 bps gross, about 23% of a Rs 50k account a year. Luna, Sol 6.1 and Grok 4.7 reviews agree. (The claim that 95 of the 110 trials were an intraday search was wrong: they were the Sleeve F C1 futures campaign)
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: superseded by 2026-11-14 — Build two things on one base (the two old rules stay dropped; same-day and multi-day trading are both back on the table for the model's picks)

## 2026-11-14 — The system must work from Rs 10,000 to any amount: one target exposure, implemented as ETFs at small capital and as a stock book once tickets are large enough
- rejected: a minimum account size (Sol said about Rs 10 lakh, Grok Rs 25 lakh, for a custom stock book)
- why: Onkar wants it to work at any size. The flat Rs 15.34 DP charge and whole shares make a 30-name book uneconomic at small capital, so the implementation changes with capital, not the mandate
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: superseded by 2026-11-15 — The small-account ETF plan is dropped (working from Rs 10,000 still stands)

## 2026-11-14 — Autonomy is earned in steps: suggest, then approve-to-send, then trade itself within limits; promotion is judged on a scorecard, not win rate
- rejected: going straight to autonomous orders; promoting on "accuracy" of paper trades
- why: Onkar wants suggestions first and self-trading once paper results are good. Win rate was 46% on both failed rules and says little; the scorecard is excess return over the benchmark after costs and tax, paper-versus-real fill gap, and clean reconciliation
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Benchmark is a broad Nifty index fund plus a Nifty200 Momentum 30 fund; gates measure excess over them after costs and personal tax
- rejected: judging per-trade bps against zero, with no benchmark and no tax (the current gate)
- why: a long-only book can pass on a rising market alone. Onkar: "anything that will improve our system"
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-11-14 — Kite login stays a daily one-tap human step; no token means the book holds and no order is sent
- rejected: automating password and TOTP
- why: the access token expires each morning and automating the login risks the account. Onkar accepted the daily step
- source: T3 thread "Audit and Improve Autonomous Trading"
- status: active

## 2026-10-23 — A split found after its ex-session already settled stays an open item on ticket 20, not fixed in PR #8
- rejected: tracking which actions a holding's quantity already includes
- why: it only happens if NSE adjusted prev_close that day (0 of 140 ex-sessions in stage 0), main has the same gap, and the real fix is a bigger change
- source: T3 thread "Visual PRs and Statuses"
- status: active

## 2026-10-23 — With nightly actions on, the signal job builds with stage 0 factors, merges against that fresh panel, and rebuilds only if the merged events differ
- rejected: merging against the previous night's live panel with a single build
- why: the previous panel lacks today's ex-session, so an action on today's ex-date was left out and the move guard could miss it (Sol P1). The second build only runs on nights where the events change
- source: T3 thread "Visual PRs and Statuses"
- status: active

## 2026-10-23 — A nightly merge with a moved-ex-date revision or a new conflicting or ambiguous ratio alerts and falls back to stage 0 factors
- rejected: not stated
- why: the move guard then halts the name, which is the safe outcome
- source: T3 thread "Visual PRs and Statuses"
- status: active

## 2026-10-22 — A revised corporate action replaces its original for the same security, ex-date and kind; the original goes to review as superseded_by_revision
- rejected: withholding both as conflicting_factors; keeping `factor` in the dedupe key
- why: the brief said only the revision applies; the original goes to review so nothing vanishes from the audit trail
- source: T3 thread "Overnight A: Greptile fixes from PR #1"
- status: active

## 2026-10-22 — Intent answers close at 08:40 IST on the session after the signal; the Telegram text says 08:40 too
- rejected: taking answers up to the 08:45 place job
- why: answers must never race the 08:45 place job. Telegram presses are still accepted until 08:45, because Telegram gives no reliable press time
- source: T3 thread "Overnight C: local approvals and macOS notifications"
- status: active

## 2026-10-22 — Holdings rescale against the current panel's factor on their last settled session, falling back to the stored last_factor
- rejected: comparing today's factor with the last_factor stored from the entry event
- why: a split first seen mid-hold never rescaled the holding, so a 1:1 split showed as a silent 50% loss. The backtest passes no lookup, so its results don't change
- source: T3 thread "Overnight B: ticket 20 rescale fix and nightly actions (off)"
- status: active

## 2026-10-22 — Kairos code work runs in its own worktree and PR, built by Haiku 5.5 on high and reviewed by Codex Sol 6.1 high, with every decision made in the prompt
- rejected: not stated
- why: Onkar wanted most of the work done by morning, with no questions
- source: T3 thread "Visual PRs and Statuses"
- status: superseded by 2026-11-15 — Kairos AI execution is owner-authorized under one-coordinator, bounded-worker orchestration

## 2026-10-22 — Intent approvals move to a local dashboard page with macOS notifications; Telegram stays as an optional channel
- rejected: WhatsApp; Telegram as the only channel
- why: WhatsApp's Cloud API needs Meta business verification, approved message templates, and a public webhook server to receive button presses (it has nothing like Telegram's getUpdates polling). Onkar prefers local and will use a Grok bot for a daily reminder
- source: T3 thread "planned PRs in visuals" (overnight run)
- status: active

## 2026-10-22 — Ticket 20's nightly NSE corporate-actions request is built but off by default; the holding-rescale fix ships on
- rejected: leaving ticket 20 unbuilt until the network approval
- why: the request needs Onkar's network approval, so turning it on is his one-line switch; the rescale fix is safe without it
- source: T3 thread "planned PRs in visuals" (overnight run)
- status: active

## 2026-10-22 — Paper accounts use approval=auto: unanswered Telegram intents are sent, and Reject skips them
- rejected: approval=required for paper accounts
- why: the plan said "auto paper now", and auto still exercises the Approve/Reject buttons
- source: T3 thread "Kairos stage 2: daily paper runner (PR 3)"
- status: active

## 2026-10-22 — Kite dry-run (ticket 17) writes a dry-run log instead of separate dry-run accounts
- rejected: separate dry-run accounts
- why: not stated
- source: T3 thread "Kairos stage 2: daily paper runner (PR 3)"
- status: active

## 2026-10-22 — kiteconnect is imported lazily and installed only through the optional `kite` extra
- rejected: installing kiteconnect as a regular dependency
- why: it pulls 19 packages from PyPI, including twisted and autobahn
- source: T3 thread "Kairos stage 2: daily paper runner (PR 3)"
- status: active

## 2026-10-22 — Kite login runs on the Mac through a local callback (127.0.0.1:5010/kite/callback); phone login waits for the stage 5 server
- rejected: logging in to Kite from a phone now
- why: not stated
- source: T3 thread "Ticket 17: Kite adapter dry-run"
- status: active

## 2026-10-22 — Kite dry-run picks BUY lines by the ORDER_SENT event's own session
- rejected: comparing the intent's signal_session to the previous session
- why: matches the runner's event log and avoids treating an older sent intent as today's order
- source: T3 thread "Ticket 17: Kite adapter dry-run"
- status: active
