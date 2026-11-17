# Ticket 04: owner service software and activation checklist

This is software for later owner activation, not a running vault or permission to
open one. Ticket 04 is incomplete. Tickets 05 and 17 remain blocked until the
owner accepts isolation and separately authorized migration evidence. Ticket 30
alone governs the much later final blind test. Do not use a final-test request
to check this installation.

## What ships

- `kairos_data/owner_daemon.py`: one dedicated Linux process, one runtime lock,
  one bounded AF_UNIX listener, serialized clients and signal-safe cleanup.
- `owner_protocol.py`: four-byte big-endian length plus UTF-8 JSON, protocol 1.
  A connection carries exactly one request and must half-close its write side.
  No paths outside the frozen relative pack identifiers, commands, pickle,
  client identity overrides, discovery or passed file descriptors are accepted.
- `protected_io.py`: descriptor-relative, no-follow traversal and regular-file
  checks. Every root ancestor is root-controlled; private directories belong
  to the service. Pack files must be singly linked, service-owned and deny
  group/other access. Renaming a pathname does not redirect an opened descriptor.
- `ops/owner-service/`: isolated bootstrap, inactive hardened unit, setup and
  rollback plans with explicit owner-only apply. No dependencies are fetched.

Only a kernel root peer is accepted. Linux `SO_PEERCRED` binds the connection;
`SO_PASSCRED`/`SCM_CREDENTIALS` additionally bind each received request fragment
to that same PID/UID/GID. Missing/mismatched credentials, ancillary handles,
truncation, extra bytes and slow/incomplete requests fail closed. The trusted
root client must not delegate its connected socket or resulting data. Root,
the OS/kernel/system Python, and the dedicated service identity are trusted;
this is not protection against a malicious administrator or compromised kernel.
The dedicated account must not run arbitrary training or interactive programs.

`health` reports only protocol/configuration state. `canary` creates/reads only
the fixed benign `audit/isolation-canary`, not a pack. `final-test` is disabled
in the installed configuration. No training or live-input RPC is provided:
ordinary date-safe readers remain separate and gain no service filesystem access.

For a future authorized final test, the server derives requester identity,
freezes the ordered daily/minute selection and binds its canonical manifest and
payload digests. One durable, locked, file-and-directory-synced style audit
precedes selected-pack resolution. All selected snapshots validate before the
first `verified` response or any row. Any failure after audit consumes that
style opening; neither client nor server retries automatically. Damaged or
incomplete audit records refuse further authorization. Do not erase an audit
to recover from a failure; the owner must review it.

## Bounds and recovery

Requests: 256 KiB, 256 packs, identifiers at most 1,024 UTF-8 bytes, five seconds
including the required write-side EOF. Responses: 2 MiB per frame and 9 GiB
per connection, with a 900-second total session deadline (also armed during
verification). JSONL source rows are at most 1 MiB of serialized bytes,
including the terminating newline; writers reject larger rows before publishing
and readers enforce the same limit. Responses are flat JSON
records, not a model evaluator or scores.

Retained verified snapshots: at most 8 GiB total and an aggregate 8 MiB spool
memory allowance. Disk availability further reduces the byte allowance with a
64 MiB reserve. These are bounds, not actual corpus capacity measurements.
Parquet uses bounded batches; total process memory is additionally limited by
the unit, not by a claim that Arrow uses only the spool allowance. The unit caps
memory at 1 GiB, descriptors at 1,024, tasks at 16, and native worker threads.

Snapshot rollover uses anonymous/unlinked temporary files in the pinned private
snapshot directory; close, disconnect, signal exit and process death release
them. The session closes unread snapshots as well as partially consumed ones.
SIGTERM exits the current request; systemd kills remaining processes after 30
seconds. A retained style audit is never rolled back. A stale socket is removed
only after taking the daemon lock and checking its type, identity and mode.
Physical power-loss recovery and real filesystem/mount behavior remain owner
acceptance work; synthetic tests do not establish them.

## Protected layout

| Path | Required trust / access |
| --- | --- |
| `/opt/kairos-owner/releases/RELEASE` | root:root directories 0555; explicit source files and receipt 0444; no links |
| `/opt/kairos-owner/deps` | optional owner-staged root:root 0555 tree; files without write/special bits; no links |
| `/etc/kairos-owner/config.json` | root:root 0444 in root-controlled 0555 directory |
| `/var/lib/kairos-owner` | root:root 0755 |
| `packs`, `audit`, `snapshots` under that parent | dedicated service UID / primary GID, directories 0700 |
| `/run/kairos-owner` | systemd RuntimeDirectory, service-owned 0700; socket 0600 |

All ancestors must be root-controlled and nonsymlink directories. Setup rejects
conflicts instead of repairing/adopting them. The immutable release does not
reference a mutable checkout or the owner's development `.venv`. Bootstrap runs
under `/usr/bin/python3 -I -S -B`, validates the release and optional dependency
tree before importing service code, and adds only descriptor-pinned roots to
the isolated standard-library search path. No `.pth` or site startup processing
runs. The unit clears Python/loader injection variables, denies IP networking,
uses read-only code/config/packs mounts and permits writes only to the three
private operational directories. No service is installed by a code merge.

## Owner checklist: prepare, do not open

All following provisioning/activation steps are **owner-only, deferred**. They
have not been executed by the agent. Use a real host reviewed for this purpose;
do not repurpose a training identity as the service account.

1. Accept the exact reviewed/merged source SHA. Choose an existing dedicated
   non-root account with no training workload or interactive login, and the
   different existing training account. Account creation, sudo policy, trusted
   system Python and filesystem/mount review belong to the owner. Recheck disk
   before preparing dependencies or proposing any later migration.
2. Export that exact SHA's `kairos_data` and `ops/owner-service` into an
   owner-controlled staging directory. Review `manage.py`, `bootstrap.py`, the
   shell wrappers and unit there. Run the staged tool itself from a root-owned,
   non-writable location when applying; a saved plan cannot protect a replaced
   installer. Resolve any source symlinks rather than following them. The plan
   captures exact allowlisted bytes and digests and rejects changed bytes on apply.
3. Stage compatible `pyarrow` and its complete dependencies for the actual
   `/usr/bin/python3` ABI in `/opt/kairos-owner/deps`, under the documented
   ownership/modes. Do not copy the development `.venv` wholesale or use links
   back to it. Dependency acquisition/sealing is a separate owner operation,
   not an action this repository runs. Startup requires an in-memory synthetic
   pre-2025 Parquet round-trip; missing/broken dependencies fail before binding.
4. Generate and inspect a plan without `--apply`. This mode reads only explicit
   source files, not production paths, identities, packs or vault contents:

   ```bash
   /usr/bin/python3 -I -S -B /ROOT_STAGING/ops/owner-service/manage.py setup \
     --source /ROOT_STAGING --release REVIEWED_SHA \
     --service-user SERVICE_ACCOUNT --service-uid SERVICE_UID \
     --training-uid TRAINING_UID > setup-plan.json
   ```

5. After review, seal that exact plan root:root 0444 in a root-controlled
   directory. As root, repeat the identical command with
   `--apply --approved-plan /ROOT_CONTROLLED/setup-plan.json`. Setup checks
   existing identities, ownership, optional dependencies and systemd state;
   it serializes management writes and refuses an active/enabled/foreign unit,
   aliases, drop-ins or conflicting files. It installs or verifies exact files
   but **never reloads, enables or starts** the unit. Repeating the same plan is
   idempotent. Interrupted temporary publication requires owner inspection;
   it is deliberately not repaired or deleted automatically.
6. Inspect installed config and unit: final testing must still be `false`.
   Run the installed bootstrap with config plus `runtime-check`; it reads no
   pack and prints only the synthetic dependency check. Run `systemd-analyze
   verify` on the installed unit and inspect effective ownership/mounts/limits.
   A passing development-host unit syntax check is not deployed isolation.
7. Only after explicit owner approval, run `systemctl daemon-reload`, then
   `systemctl start kairos-owner.service`. Do not enable at boot until the
   following acceptance is recorded. No socket-activation unit is involved.

## Owner checklist: synthetic isolation acceptance

As root, using the installed reviewed release:

```bash
/usr/bin/python3 -I -S -B /opt/kairos-owner/releases/REVIEWED_SHA/bootstrap.py \
  /etc/kairos-owner/config.json health
/usr/bin/python3 -I -S -B /opt/kairos-owner/releases/REVIEWED_SHA/bootstrap.py \
  /etc/kairos-owner/config.json canary
```

Record that health reports final testing disabled. Canary must report the
SHA-256 of the literal `kairos-owner-synthetic-isolation-canary-v1\n` only.
From the **actual training account**, record denied attempts to open that
synthetic canary or connect to `/run/kairos-owner/owner.sock`. Check that the
training account cannot modify installed code, dependencies, config, unit or
their ancestors; verify no writable ACL, shared group, inherited descriptor,
sudo capability or other path defeats the separation. Test stop/start and
stale-socket recovery using these operations only. Record effective UID,
unit properties, expected denials and restart outcomes without secrets.

Do **not** list or read pack contents, invoke either filesystem opening entry
point, enable the final-test operation, or manufacture a held-out fixture for
acceptance. The public training refusal and live-input guards have separate
synthetic regression evidence; no training/live reader gains this socket.

## Rollback

Generate a separate owner-reviewed rollback plan:

```bash
/usr/bin/python3 -I -S -B /ROOT_STAGING/ops/owner-service/manage.py rollback \
  --release REVIEWED_SHA > rollback-plan.json
```

Seal it root:root 0444 under root-controlled ancestors. As root repeat with
`--apply --approved-plan /ROOT_CONTROLLED/rollback-plan.json`. Rollback proves
the managed receipt/unit hash, rejects foreign fragments/drop-ins/aliases,
stops and disables, confirms no remaining processes, removes only its unit,
and reloads the manager. Manager failures stop removal. Repeated completed
rollback is safe. It preserves every release, config, account, pack, snapshot
directory and audit record; it never deletes data or silently resets style
authorization. Reinstalling requires the same reviewed immutable bytes, or
separate owner review of conflicts/upgrades. Keep the rollback tool/plan even
after installation; do not depend on a mutable development checkout.

## Evidence still required

Deployed isolation/canary acceptance; actual daily partition migration counts
and checksums without scoring or opening blind contents; storage capacity and
physical crash/restore evidence; owner acceptance of ticket 04. Those need
separate authorization and accepted prerequisites. Minute packing/transfer is
ticket 17, not this continuation. Ticket 30's frozen-model/style approvals and
single blind test are much later. No model performance, training, paper elapsed
record, broker operation or deployment acceptance is claimed here.
