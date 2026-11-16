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
legacy one-pack wrapper requires the same frozen one-pack metadata and cannot
infer authorization from a path.

`owner_session` contains pure metadata validation only; it grants no access.
Tests cover that helper and the policy-only durable audit record. The
owner-only filesystem opening functions are intentionally not executed by
these tests and remain **UNVERIFIED**. This code does not implement enforced
OS/IPC protection. Ticket 04 remains incomplete; tickets 05 and 17 remain
blocked pending accepted owner-controlled opening, isolation, and authorized
real migration evidence. Do not use this implementation to open the final-test
entry point outside owner-controlled acceptance.
