"""SQLite schema and connections for the news corpus.

Two databases, separated by concern:

* The **corpus DB** (``data/corpus.db``) holds the clean relational core — ``articles``
  and ``chunks``. It stays small, so it is fast to copy and inspect, and it is the
  artifact we actually reason about. Git-ignored: article bodies are the outlets'
  copyright.

* The **HTML cache DB** (``data/cache/html.sqlite``) holds the raw downloaded HTML, one
  gzip-compressed row per URL. Keeping it in its own file means re-extracting bodies
  never re-downloads, and the bulky raw HTML never bloats the corpus DB. Git-ignored and
  fully disposable — delete it and the corpus DB is unaffected.

This module owns only the schema and connections. The ingest/fetch/extract/dedup logic
that fills these tables lives in the sibling modules (Phase 1).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/corpus.db")
DEFAULT_CACHE_PATH = Path("data/cache/html.sqlite")

# --- Corpus DB: the clean relational core -----------------------------------------

CORPUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT,
    url               TEXT NOT NULL UNIQUE,   -- identity + dedup-by-URL + cache key
    domain            TEXT,                   -- source domain (bbc.com, espn.com)
    published_at      TEXT,                   -- ISO-8601 UTC; drives temporal retrieval
    language          TEXT,
    body_full         TEXT,                   -- NULL until extracted (resume contract)
    body_valid        INTEGER,                -- 0/1 from is_valid_body
    invalid_reason    TEXT,                   -- why body_valid=0: too_short|spam_phrase|off_topic
    content_hash      TEXT,                   -- hash of normalized body: exact-dup anchor
    dup_of            INTEGER REFERENCES articles(id),  -- canonical id if near-duplicate
    extractor_version TEXT,                   -- which extractor produced body_full
    fetched_at        TEXT                    -- when the HTML was downloaded
);

CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_dup_of    ON articles(dup_of);
-- Partial index over unscraped rows: keeps the `WHERE body_full IS NULL` resume scan cheap.
CREATE INDEX IF NOT EXISTS idx_articles_pending   ON articles(id) WHERE body_full IS NULL;

CREATE TABLE IF NOT EXISTS chunks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id     INTEGER NOT NULL REFERENCES articles(id),
    chunk_index    INTEGER NOT NULL,          -- position within the article (0,1,2,...)
    text           TEXT NOT NULL,             -- what gets embedded and retrieved
    char_start     INTEGER,                   -- offset into body_full: exact-passage citation
    char_end       INTEGER,
    token_count    INTEGER,                   -- chunk size in tokens (cost accounting)
    chunk_strategy TEXT NOT NULL              -- 'fixed' | 'semantic' (ablation knob)
);

CREATE INDEX IF NOT EXISTS idx_chunks_article  ON chunks(article_id);
CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks(chunk_strategy);
"""

# --- HTML cache DB: bulky, disposable, separate -----------------------------------

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_html (
    url         TEXT PRIMARY KEY,   -- matches articles.url
    html_gz     BLOB,               -- gzip-compressed raw HTML
    status_code INTEGER,            -- HTTP status of the fetch
    fetched_at  TEXT                -- ISO-8601 UTC
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with the project's standard pragmas.

    WAL journaling survives interruptions cleanly (good for a resumable scraper), and
    foreign-key enforcement is off by default in SQLite, so we turn it on per-connection.
    """
    conn = sqlite3.connect(Path(path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _ensure_column(conn, table: str, column: str, decl: str) -> None:
    """Add a column if it's missing. SQLite has no `ADD COLUMN IF NOT EXISTS`.

    Lets the schema evolve without forcing a rebuild of an existing corpus DB.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_corpus(path: str | Path = DEFAULT_DB_PATH) -> Path:
    """Create the corpus DB (articles + chunks) if it does not exist. Idempotent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(CORPUS_SCHEMA)
        # Migrations for DBs created before a column existed.
        _ensure_column(conn, "articles", "invalid_reason", "TEXT")
        conn.commit()
    finally:
        conn.close()
    return path


def init_cache(path: str | Path = DEFAULT_CACHE_PATH) -> Path:
    """Create the HTML cache DB (raw_html) if it does not exist. Idempotent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(CACHE_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return path
