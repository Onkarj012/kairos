# Kairos data packs

`write_pack` keeps version 1 JSONL as the default and accepts
`encoding="parquet"` for compact columnar packs using the declared runtime
`pyarrow` dependency. The manifest names the encoding explicitly; readers do
not infer it from a filename. Old version 1 manifests without `encoding` remain
JSONL. Parquet manifests include an ordered scalar schema.

Pack creation writes to a temporary directory and publishes it with an atomic
rename. The manifest records the partition, kind, row count, date range, and
SHA-256 of the exact encoded bytes. Readers validate the selected pack's
checksum, schema, row count, and date coverage before yielding from the same
private byte snapshot. Identical writes, including publication collisions,
are safe to retry; conflicting destinations are refused. Source files are
never changed or deleted.

Training and live reads keep their existing date boundaries and partitions.
Training refuses requests on or after `2025-01-01` before opening a pack and
accepts only `pre2025`. Live input packs remain daily, flat, scalar-only, and
answer-free; the reader selects up to 60 completed sessions strictly before
`as_of`, then projects only requested columns. Daily and minute packs use the
same encoding support. Date values remain input strings, preserving exact
minute timestamps; this layer does not adjust prices or score rows.

Parquet reads and writes use bounded batches/row groups (default 4096 rows;
configurable from 1 to 65536). Snapshot verification uses an 8 MiB in-memory
spool threshold and spills larger packs to temporary disk, so a full pack copy
must fit in available temporary storage.

The compact-size regression compares only a small repeated synthetic fixture
against its JSONL form. It is not a capacity estimate for 439 million real
rows, does not establish that the Mac's 22 GB source fits in available space,
and does not record a migration or actual source-data size.

`owner_service.open_final_test_session` accepts only a nonempty, explicitly
ordered frozen selection of relative pack identifiers. Each entry binds daily
or minute kind plus canonical manifest and content SHA-256 digests. The request
hash and durable one-per-style audit line include the complete ordered
selection. It does not enumerate the vault. After authorization is durable,
the owner path verifies every selected blind pack into private snapshots before
returning any row iterator. Selection is limited to 256 packs, 8 GiB of
aggregate snapshot bytes, and 8 MiB of aggregate in-memory spool space. The
returned `OpenedSession` exposes `audit` and ordered `packs`; use it as a
context manager or call `close()` explicitly. Session cleanup closes all row
iterators and snapshots, including unread packs, and attempts every cleanup
even if one fails. Individual row iterators also support idempotent `close()`
before iteration starts and close on exhaustion or iteration failure. The
legacy one-pack wrapper requires the same frozen one-pack metadata and cannot
infer authorization from a path. Its return tuple is unchanged; close its row
iterator explicitly when stopping early or leaving it unread.

`owner_session` contains pure metadata validation only; it grants no access.
Tests cover that helper, the policy-only durable audit record, and cleanup
using empty in-memory streams with synthetic iterators, not pack fixtures. The
owner-only filesystem opening functions are intentionally not executed by
these tests and remain **UNVERIFIED**. This code does not implement enforced
OS/IPC protection. Ticket 04 remains incomplete; tickets 05 and 17 remain
blocked pending accepted owner-controlled opening, isolation, and authorized
real migration evidence. Do not use this implementation to open the final-test
entry point outside owner-controlled acceptance.

## Offline Linux owner-boundary candidate

`owner_boundary` is a helper for later owner-service deployment planning. It
requires different non-root integer UIDs for the owner service and training
process. Its authorizer reads PID/UID/GID using Linux `SO_PEERCRED` from an
already-connected `AF_UNIX` stream handle. Only a kernel-reported UID 0 peer
can represent owner approval; the requester's value must equal the canonical
identity derived from those credentials, and a nonblank reason is required.
The service process's effective UID must match its configured service UID.
No environment variable or caller-supplied owner name grants access.

The adapter implements the existing `OwnerAuthorizer` method shape and does
not alter any opening function. Supplying a fake Python object or changing
arguments inside the trusted service process is not authentication against
malicious code in that process. This helper creates, binds, connects, or accepts
no sockets and does not isolate the process or protect files on its own.

Protected-layout validation consumes immutable, supplied `PathMetadata`
descriptors only. It checks root ownership and non-writable code/config files,
root-owned non-group/world-writable ancestor directories, and service-owned
owner-only data, audit, and runtime directories; it rejects missing ancestors,
symlinks, unsafe modes, wrong owners, and wrong file types. These checks validate
the supplied evidence. They are not race-proof enforcement, filesystem
provisioning, or proof of live ownership, and this code never stats or opens a
path. Deployment must safely derive metadata and enforce protected paths before
loading service code. The protected root and vault have not been inspected.

This is an architecture candidate only. Ticket 04 remains **INCOMPLETE**;
tickets 05 and 17 remain **BLOCKED**. No real isolation or authenticated
service is claimed. Owner acceptance prerequisites remain separate: deploy the
service under the dedicated identity, keep training unable to read protected
paths, establish a safely enforced IPC endpoint and layout, then review and
accept the deployment before any authorized owner operation.
