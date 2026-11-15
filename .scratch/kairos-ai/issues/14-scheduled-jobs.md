# 14: Scheduled daily jobs on this machine

**What to build:** The daily cycle runs by itself on this Linux machine. Timers replace the old macOS jobs removed in ticket 02. Pieces:
- an evening job (collect the day's NSE files, build today's rows, run the model, make picks);
- a morning job (publish picks before 09:00, plus a reminder for the one-tap Kite login);
- an evening report job.

A lock stops two copies of a job running at once. An alert from outside the machine fires when a job does not report in on time. With no login token the system places nothing and simply holds. Plan: `../spec.md` sections 9 and 11; `../retained-system-requirements.md`.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Timers for the evening, morning and report jobs, with an install and uninstall script.
- [ ] Starting a second copy of a running job exits at once and writes a log line.
- [ ] A killed job raises an alert from outside the machine within its window.
- [ ] The morning login reminder arrives. A missing token means hold, and the log says so.

## Infrastructure evidence only

PR 14 bot review found environment-dependent lock paths and unsafe checkout-path
substitution. Local fixes use the same home-state default for services and CLI,
and reject unsupported installer checkout paths before writes. Synthetic lock
contention and path-refusal checks pass. These changes await independent review,
push and current-head bot acceptance; the ticket remains operationally incomplete.

Round-one independent review blocked on completion-log/heartbeat ordering,
non-object heartbeat handling and uninstall leaving active services. All three
have local regression fixes; eight focused Python tests and three fake-manager
shell scenarios pass. Round-two independent approval is still pending. No host
service actions ran and no operational acceptance is claimed.

See `../ticket14-evidence.md` and `ops/systemd/README.md` for the offline implementation, validation and remaining operational evidence. Three unit pairs, install/uninstall scripts, a shared nonblocking lock and fail-closed adapters are implemented. Coordinator fixed the service script's repository import path and changed the standalone default lock away from system `/run/lock` to the user runtime/private state directory. Six focused synthetic tests pass; an isolated CLI invocation from an unrelated temporary directory exits held (2), with no success heartbeat. No unit was installed or enabled. External monitor delivery, actual reminder delivery and real data/model/picks/report handler wiring remain absent, so ticket 14 is incomplete and 15 remains blocked.
