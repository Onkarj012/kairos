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
