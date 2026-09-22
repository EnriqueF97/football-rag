"""Corpus snapshots to Google Cloud Storage — durability, not remote querying.

The corpus is 188 MB of SQLite plus a 1.8 GB HTML cache, both git-ignored, and it exists
on exactly one laptop. Parts of it are **not re-scrapeable**: Common Crawl indexes rotate
and Media Cloud results drift, so a lost disk is a permanently different corpus, not an
afternoon of re-running `make ingest`. That is the problem this module solves.

**It is a snapshot sync, not a network filesystem.** SQLite is a local-file database: it
assumes POSIX byte-range locking, which object storage does not provide. Querying the
`.db` in place — over a GCS FUSE mount or HTTP range requests — either corrupts the file
or turns a 2 ms query into seconds of round-trips. So:

    push:  checkpoint WAL  ->  upload corpus.db + manifest under a timestamped prefix
    pull:  download        ->  verify against the manifest  ->  atomically replace local

If remote querying is ever genuinely needed, that is a migration to Cloud SQL, not a
mount. See AGENTS.md §12.

Two design decisions worth stating:

* **Snapshots are timestamped, never overwritten.** One bad push must not destroy the only
  good copy. Old snapshots are aged out by a bucket lifecycle rule, not by this code.
* **Every snapshot carries a manifest.** GCS checksums prove the *bytes* arrived intact;
  they cannot prove the right *database* arrived. Row counts can. A pull that silently
  restores last month's corpus is exactly the class of failure this project keeps meeting:
  it runs, it reports success, and the number it produces is wrong.

The bucket is **private** — backup, not redistribution. Article bodies still never enter
git (AGENTS.md §11).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from newsrag.corpus import db

#: Bucket name, e.g. "my-rag-football-corpus". No "gs://" prefix.
BUCKET_ENV = "NEWSRAG_GCS_BUCKET"

#: All snapshots live under this prefix, so the bucket can hold other things later.
SNAPSHOT_PREFIX = "corpus-snapshots"

#: Written beside each uploaded .db. See the module docstring for why.
MANIFEST_NAME = "manifest.json"


def _bucket_name(override: str | None = None) -> str:
    """Resolve the target bucket: explicit argument first, then the environment.

    Fails loudly rather than defaulting, because a wrong bucket is a silent no-op that
    looks like success.

    Expected:
        _bucket_name("my-bucket")                  -> "my-bucket"
        NEWSRAG_GCS_BUCKET=foo, _bucket_name(None) -> "foo"
        nothing set                                -> RuntimeError
    """
    name = override or os.environ.get(BUCKET_ENV)

    # Raise RuntimeError naming BUCKET_ENV when `name` is empty — the message should tell
    # the user how to fix it, not just that something is missing.

    raise NotImplementedError


def _client():
    """Return an authenticated GCS client. (given — this is library boilerplate)

    Uses Application Default Credentials, written by:
        gcloud auth application-default login
    """
    from google.cloud import storage

    return storage.Client()


def _checkpoint_wal(conn) -> None:
    """Fold the write-ahead log back into the main .db file before snapshotting.

    SQLite in WAL mode keeps recently committed pages in `corpus.db-wal`, not in
    `corpus.db`. Uploading the .db alone would ship a database missing its most recent
    writes — and it would look fine, because it opens and queries normally.

    Expected: returns None; afterwards `corpus.db-wal` is 0 bytes.
    """
    # PRAGMA wal_checkpoint returns one row: (busy, log_pages, checkpointed).
    busy, _log_pages, _checkpointed = conn.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()

    # busy == 1 means SQLite could not get the lock — another connection is holding it and
    # the snapshot would be incomplete. That must RAISE, not warn: a partial snapshot that
    # uploads successfully is worse than a failed upload.

    raise NotImplementedError


def _manifest(conn) -> dict:
    """Describe the database being snapshotted, so a pull can verify what it got.

    Args:
        conn: open corpus DB connection.
    Returns:
        A JSON-serialisable dict.

    Expected:
        {
          "created_at":   "2026-09-21T18:04:11Z",
          "total_rows":   33594,
          "usable_rows":  25215,
          "max_id":       75108,
          "schema_version": 1,
        }
    """
    # `_USABLE` is the project-wide definition of the indexable corpus. Import it rather
    # than retyping the predicate — it must never drift from smell.py's copy.
    from newsrag.corpus.smell import _USABLE

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build and return the dict above. Three scalar queries over `articles`:
    #   - total rows
    #   - rows matching _USABLE
    #   - MAX(id)
    # Keep schema_version literal 1 for now; it exists so a future schema change can be
    # detected on pull instead of producing confusing errors.

    raise NotImplementedError


def snapshots(bucket: str | None = None) -> list[dict]:
    """List available snapshots, newest first. (WORKED EXAMPLE — copy this shape)

    This one is done for you: it shows the google-cloud-storage idiom you need in `push`
    and `pull` — get a bucket handle, list blobs under a prefix, read one back.

    Returns:
        One dict per snapshot, each the stored manifest plus its snapshot id.

    Expected:
        [{"id": "2026-09-21T18-04-11Z", "total_rows": 33594, "usable_rows": 25215, ...},
         {"id": "2026-09-14T09-22-40Z", ...}]
    """
    client = _client()
    gcs_bucket = client.bucket(_bucket_name(bucket))

    found = []
    for blob in client.list_blobs(gcs_bucket, prefix=f"{SNAPSHOT_PREFIX}/"):
        if not blob.name.endswith(f"/{MANIFEST_NAME}"):
            continue
        # "corpus-snapshots/<id>/manifest.json" -> "<id>"
        snapshot_id = blob.name.split("/")[-2]
        manifest = json.loads(blob.download_as_text())
        found.append({"id": snapshot_id, **manifest})

    return sorted(found, key=lambda m: m["id"], reverse=True)


def push(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    bucket: str | None = None,
    dry_run: bool = True,
) -> str:
    """Upload a consistent snapshot of the corpus DB.

    Args:
        db_path: local SQLite corpus.
        bucket:  target bucket; falls back to $NEWSRAG_GCS_BUCKET.
        dry_run: when True, report what *would* be uploaded and upload nothing.
    Returns:
        The snapshot id (its object prefix), e.g. "2026-09-21T18-04-11Z".

    Expected:
        dry run  -> prints size + manifest, returns the id it would have used
        real run -> corpus-snapshots/<id>/corpus.db and .../manifest.json exist
    """
    db_path = Path(db_path)
    conn = db.connect(db_path)

    # 1. Make the file consistent, then describe it.
    _checkpoint_wal(conn)
    manifest = _manifest(conn)

    # 2. Build the snapshot id from manifest["created_at"]. It becomes an object path, so
    #    ":" must go — "2026-09-21T18:04:11Z" -> "2026-09-21T18-04-11Z".
    #    Sortable as a plain string, which is what `snapshots()` relies on.

    # 3. If dry_run: print the id, db_path.stat().st_size, and the manifest, then return
    #    the id WITHOUT touching the network. Uploading 188 MB is not reversible on a
    #    metered connection, and the working agreement says dry run first.

    # 4. Otherwise upload two objects under f"{SNAPSHOT_PREFIX}/{snapshot_id}/":
    #       corpus.db      <- blob.upload_from_filename(db_path)
    #       manifest.json  <- blob.upload_from_string(json.dumps(manifest, indent=2),
    #                                                 content_type="application/json")
    #    Upload the .db FIRST and the manifest LAST: the manifest is the marker that the
    #    snapshot is complete, so a crash mid-upload leaves an unlisted, ignorable
    #    directory rather than a manifest promising a database that isn't there.

    raise NotImplementedError


def pull(
    snapshot_id: str | None = None,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    bucket: str | None = None,
    dry_run: bool = True,
) -> str:
    """Download a snapshot and replace the local corpus DB.

    **Destructive**: this overwrites 188 MB of local state, so it dry-runs by default.

    Args:
        snapshot_id: which snapshot; None means the newest.
        db_path:     where to write locally.
        bucket:      source bucket; falls back to $NEWSRAG_GCS_BUCKET.
        dry_run:     when True, report the comparison and write nothing.
    Returns:
        The snapshot id that was (or would be) restored.

    Expected:
        dry run -> prints "local: 33594 rows  ->  snapshot 2026-09-21...: 33594 rows"
        real    -> db_path replaced; corpus.db-wal and -shm removed
    """
    db_path = Path(db_path)
    available = snapshots(bucket)

    if not available:
        raise RuntimeError(f"no snapshots found in gs://{_bucket_name(bucket)}")

    # 1. Resolve which snapshot to restore: the newest when snapshot_id is None, otherwise
    #    the matching entry. An unknown id must raise and list what IS available — a
    #    typo'd id that silently restores the newest snapshot is a nasty failure.

    # 2. Show the comparison BEFORE doing anything: local row count (if db_path exists)
    #    against the chosen manifest's. This is the whole point of the manifest — the user
    #    should see "33594 -> 28100" and stop, rather than discover it three commands later.

    # 3. If dry_run: return the id here, having written nothing.

    # 4. Download to a temporary path next to the target, NOT over it:
    #        tmp = db_path.with_suffix(".db.tmp")
    #        blob.download_to_filename(tmp)
    #    A download that dies halfway must not leave a corrupt corpus.db behind.

    # 5. Replace atomically with os.replace(tmp, db_path), then DELETE the stale sidecars
    #    db_path.with_suffix(".db-wal") and ".db-shm" if they exist. They belong to the
    #    database you just overwrote; leaving them beside a different .db risks corruption.

    # 6. Reopen and verify: the restored row counts must match the manifest. Raise if not.
    #    Verified beats assumed — a restore that quietly half-worked is the failure this
    #    whole module exists to prevent.

    raise NotImplementedError
