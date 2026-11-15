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
