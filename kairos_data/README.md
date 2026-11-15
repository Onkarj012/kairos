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

This package does not implement multi-pack owner opening or enforced OS/IPC
protection. Those remain required before ticket 04 can be accepted; tickets 05
and 17 remain blocked pending accepted isolation and authorized real migration
evidence. Do not use this implementation to open the final-test entry point.
