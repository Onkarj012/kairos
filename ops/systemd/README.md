# Kairos scheduled jobs

These are user-level Linux systemd oneshot jobs. The install script copies unit
files with the current checkout path substituted; review the resulting files
before taking the owner action to reload systemd and enable timers. Install
does not enable or start anything. Uninstall disables and stops all three timers,
then stops their services before removing any unit files. A manager error aborts
removal and is reported to the caller; it is not treated as successful cleanup.

## Schedule and deadlines

All calendar times are Asia/Kolkata (IST). Evening build and picks start at
18:45 with up to five minutes of random delay; morning publish/reminder starts
at 08:15 with up to two minutes of delay; evening report starts at 20:30 with
up to five minutes of delay. External monitor deadlines should allow the
maximum jitter plus runtime: 19:30 for build/picks, 08:45 for morning, and
21:15 for report. These are daily wall-clock deadlines, including non-trading
days until an owner provisions a calendar-aware monitor.

## Runtime contract

Every process takes the same nonblocking lock (`%t/kairos-jobs.lock`), so these
jobs serialize with each other. A duplicate exits 75 and logs
`overlap-skipped`. Linux releases the advisory lock on normal exit, exception,
or process death. Logs are under `~/.local/state/kairos/jobs/jobs.jsonl` and
contain job names, event times and safe reason labels only. Token values,
paths, and exception messages are never logged.

Standalone CLI use defaults to the same `XDG_RUNTIME_DIR` lock directory when
available, otherwise the private job state directory; it does not require a
writable system `/run/lock`. The service entry point establishes the repository
import path without relying on ambient `PYTHONPATH`.

Successful completion atomically replaces one per-job JSON file in
`~/.local/state/kairos/jobs/heartbeats/`. Its fields are `job`, `started_at`,
`completed_at`, and `timezone`. A monitor must accept only the expected job and
timezone and a completion before its deadline. Missing files, stale timestamps,
failed/held status, and a job marked started without completion are missed
deadlines. The heartbeat is written only after the injected job handler (and,
for morning, injected reminder transport) completes and its completion log is
written. Heartbeat publication is the final success operation. The completion
log alone does not prove heartbeat publication. Non-object JSON is an invalid
heartbeat, not a monitor exception. The monitor must run
outside this host and alert on missing deadlines; the code cannot provision or
prove that monitor or its delivery path.

## Deliberate current holds

The CLI currently wires no handlers, token source, or notifier. Therefore all
three jobs truthfully log `held` and exit 2. The morning handler contract checks
an injected same-day token record in the existing JSON shape (`access_token`,
`user_id`, `login_date`) only; it never reads the real token file. A missing,
empty, malformed, or wrong-date token means hold and no handler call. Token data
is never logged. No picks, report, order callback, or success heartbeat is
fabricated. Later tickets must provide the audited data/model/paper handlers
and owner-approved reminder transport. Even then this scheduler contains no
broker/order integration.

The morning reminder itself must be delivered by a configured transport. Its
delivery cannot be established by this offline contract. A killed process
cannot run its own alert handler; the remote monitor must detect a missed
deadline and send that alert. Neither outside-host alert delivery nor actual
reminder delivery has been provisioned or proven here. Ticket 14 remains
incomplete, ticket 15 remains blocked, and ticket 32 server setup/deployment
remains owner-controlled.
