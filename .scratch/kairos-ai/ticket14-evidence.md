# Ticket 14 implementation evidence

Status: implementation only; ticket 14 remains incomplete.

## Scope delivered

- `ops/scheduled_jobs.py` defines three job names, injectable handler/notifier
  contracts, same-day injected token validation, one shared nonblocking Linux
  advisory lock, safe local logs, and atomic per-job completion heartbeats.
- `scripts/kairos_jobs.py` is an explicit entry point. It has no configured
  handlers, token source, or notifier, so production invocations currently log
  `held` and exit 2 without fabricated outputs or heartbeat success.
- `ops/systemd/` contains three IST user timers/services, install/uninstall
  scripts, deadlines and monitor contract documentation. Install copies unit
  files only; it does not reload, enable, or start systemd.
- `tests/test_scheduled_jobs.py` uses synthetic in-memory handlers, notifier,
  tokens and timestamps only.

## Validation performed

- PR 14 Greptile review at c10cefc found two further P2s. The standalone fallback
  and service lock differed when `XDG_RUNTIME_DIR` was absent; both now use one
  stable home-state default independent of runtime/log-state overrides. Services
  no longer override it. Synthetic runs with and without XDG contend on the same
  held lock. Installer now rejects unsupported checkout characters before any
  unit-directory write. Space, ampersand and percent fixtures fail closed under
  temporary paths; no real installation or manager action occurs. The new
  regressions failed before fixes. Fresh exact-head independent review and
  current-head bot acceptance remain required before merge.
- Round-one review found three P2 regressions. Coordinator reproduced both
  runtime failures with new tests before fixing them, and a fake-manager shell
  regression failed because uninstall never stopped services. Completion logging
  now precedes terminal heartbeat publication; non-mapping JSON fails freshness;
  uninstall disables all timers, stops their services, and aborts on any manager
  error before deleting definitions. The isolated Python selection now reports
  **8 passed in 0.07s**, zero safety refusals. The shell regression passes success,
  timer-error, and service-error cases using only a private fake `systemctl`.
  Shell syntax and `git diff --check` pass. No host service action occurred.
  Independent round-two review is required before PR.
- Coordinator inspected the full three-file import closure and the entry point,
  then reran the isolated selection using private TMPDIR: **6 passed in 0.06s**,
  zero safety refusals. Fixed repository imports for the service entry point
  and a user-writable standalone lock default; production adapters remain absent.
  Invoking the script under `env -i` and isolated Python from an unrelated
  temporary directory exited held (2), wrote only temporary started/held logs,
  and wrote no success heartbeat. No systemd or network action occurred.
- Added an explicit one-file ignore exception for this small evidence note;
  other scratch outputs/data remain excluded.
- Import closure audited before execution with
  `/usr/bin/python3 -I -S -B /tmp/kairos-safe-focused-check.py "$PWD" tests/test_scheduled_jobs.py`.
  Closure: `tests/test_scheduled_jobs.py`, `ops/scheduled_jobs.py`, and
  `ops/__init__.py`; no sealed module, broker, real artifact, or external module
  was reached.
- Focused synthetic tests: 5 passed. Safety refusals: none.
- `bash -n ops/systemd/install.sh ops/systemd/uninstall.sh`: passed.
- `systemd-analyze verify` over all six template-rendered units (checkout path
  substituted): passed; no service or host IPC was started.
- `git diff --check`: passed.

## Not established

Outside-host monitor provisioning and missed-deadline alert delivery are not
implemented or proven. Actual morning reminder delivery is not implemented or
proven. Model/data/picks, publish, and report handlers are not configured.
No install, timer enable/start, trading/data job, token read, notification,
external monitor, or deployment was performed. Ticket 14 is incomplete and
ticket 15 remains blocked; ticket 32 remains owner-controlled.

## Decisions for coordinator log

- Use user-level systemd units, installed only by explicit owner action; rejected
  automatic enable/start. Reason: bounded implementation scope and server setup
  remains owner-controlled. Source: ticket 14 implementation thread. Status:
  proposed.
- Serialize all three jobs with one nonblocking host-user lock; rejected
  separate per-job locks. Reason: jobs may share external account/data writers.
  Source: ticket 14 implementation thread. Status: proposed.
- Store a separate completion heartbeat per job and refresh it only after
  handler completion; rejected start-time heartbeats and one shared latest-job
  file. Reason: a killed or held job must remain visibly overdue independently.
  Source: ticket 14 implementation thread. Status: proposed.
- Keep production adapters unconfigured and fail closed; rejected placeholder
  successful pipelines. Reason: picks/model/data/report backends do not yet
  exist and no-op success would be false evidence. Source: ticket 14
  implementation thread. Status: proposed.
