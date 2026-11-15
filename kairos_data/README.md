# Kairos data packs

This package contains the offline pack format and separate readers for
pre-2025 training rows and recent live inputs. Daily and minute rows share the
same manifest and JSONL representation. A live store uses one daily pack
directory per session, named `YYYY-MM-DD`; each pack contains inputs only.

Pack creation writes to a temporary directory and publishes it with an atomic
rename. The manifest records the partition, pack kind, row count, date range,
and SHA-256 of the exact JSONL bytes. Readers verify the full selected pack
before yielding rows. Identical writes are safe to retry; a different pack at
an existing destination is refused. Source files are never changed or deleted
by this package.

`read_training_rows` rejects an inclusive `2025-01-01` request before opening
the pack. It accepts only a `pre2025` pack beneath its configured root. The
root must contain pre-2025 data only. `read_live_inputs` selects at most 60
dated session directories no later than `as_of`, opens only those packs, and
returns only explicitly requested input columns. It refuses answer-like
column names and the pack writer rejects those keys in live packs.

## Owner boundary

`kairos_data.owner_service` is intentionally not imported by the package
initializer or training reader. It provides a final-test request policy,
durable append-only audit records, a hash over model-weight bytes and canonical
settings, and an owner-service opening entry point. A request records the
timestamp, frozen style, model fingerprint, requester, and reason. The audit
log allows one opening per frozen style and is synced before the service opens
the pack.

Python visibility, private attributes, caller-supplied booleans, and file mode
bits do not protect against arbitrary code running under the same OS account.
Production must place the vault and audit log under a dedicated owner-service
OS identity, expose opening through an OS-authenticated IPC endpoint, and deny
the training/live process filesystem access to that identity's files. The
`OwnerAuthorizer` is an adapter contract for that trusted service boundary; it
is not itself an authenticator. This repository does not yet implement or
configure the daemon, IPC peer-credential adapter, or OS accounts. Ticket 30
must supply and review those deployment controls before any final test.

No source-data migration is performed here. Synthetic pack checks do not prove
the real partition, source checksums, or source row counts, and do not satisfy
ticket 04 migration evidence or unlock dependent data tickets.
