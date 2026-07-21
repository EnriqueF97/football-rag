"""Guardian Open Platform ingest — the corpus's reliable-tier anchor.

The Guardian blocks CCBot (`Disallow: /`), so it is absent from Common Crawl, and the
GDELT DOC API is no longer usable from this IP. Its own API is therefore the *only* door
to the best English football desk — and it happens to be the nicest one in the whole
pipeline: an official, key-based endpoint that legally returns the full article body.
No Cloudflare, no cookie banners, no robots gymnastics.

Division of labour (mirrors gdelt.py + fetch.py in one pass):

* metadata (title, url, published_at) -> ``articles``, exactly like the GDELT ingest;
* the article body HTML (``fields.body``) -> the ``raw_html`` cache as a status-200 row.

Bodies deliberately go through the cache rather than straight into ``body_full``: that
way ``extract.py`` processes Guardian articles through the *same* trafilatura + ftfy +
validity gate as every other source, so content hashes are comparable for dedup and an
extractor improvement re-benefits this source too. ``fetch.py`` skips these URLs
automatically (already cached).

Resume: reuses the ``ingest_log`` ledger with 28-day windows under the query key
``guardian:<section>``. Interrupt at any point; the next run continues.

Key: set ``GUARDIAN_API_KEY`` (free at https://open-platform.theguardian.com/access/).
Falls back to the shared demo key ``test`` — fine for a smoke run, too throttled for
the full backfill.
"""

from __future__ import annotations

import gzip
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from newsrag.corpus import db
from newsrag.corpus.gdelt import load_config

GUARDIAN_URL = "https://content.guardianapis.com/search"
DEFAULT_CONFIG = Path("config/football_queries.yaml")
DOMAIN = "theguardian.com"

#: ingest_log key prefix — keeps Guardian windows distinct from GDELT query windows.
_LEDGER_PREFIX = "guardian:"


def _api_key() -> str:
    key = os.environ.get("GUARDIAN_API_KEY", "").strip()
    if not key:
        print(
            "⚠ GUARDIAN_API_KEY not set — using the shared 'test' key (heavily "
            "throttled). Get a free key: https://open-platform.theguardian.com/access/"
        )
        return "test"
    return key


def search_window(
    client: httpx.Client,
    section: str,
    start: datetime,
    end: datetime,
    api_key: str,
    page_size: int = 50,
    sleep_seconds: float = 1.0,
    max_retries: int = 2,
) -> list[dict]:
    """All articles in ``section`` published in [start, end). Paginates to the end.

    The API is inclusive on both date bounds, so ``to-date`` is end minus one day —
    keeping windows half-open like the rest of the pipeline (no double-counted days;
    INSERT OR IGNORE would absorb them anyway, but the ledger stays honest).

    Retries on 429/5xx with a flat wait: the free tier's 1 req/s cap is generous enough
    that a genuine 429 here means "slow down", not GDELT-style banishment.
    """
    params = {
        "section": section,
        "type": "article",  # excludes liveblogs — minute-by-minute noise, not articles
        "from-date": start.strftime("%Y-%m-%d"),
        "to-date": (end - timedelta(days=1)).strftime("%Y-%m-%d"),
        "page-size": page_size,
        "order-by": "oldest",  # deterministic pagination within a window
        "show-fields": "headline,body",
        "api-key": api_key,
    }
    results: list[dict] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        attempt = 0
        while True:
            # Transport errors (TLS handshake timeouts, connection resets) are as
            # transient as a 429 over a minutes-long run — same retry budget.
            try:
                resp = client.get(
                    GUARDIAN_URL, params={**params, "page": page}, timeout=30
                )
            except httpx.TransportError as exc:
                if attempt >= max_retries:
                    raise
                attempt += 1
                print(f"      {type(exc).__name__} — waiting 10s (retry {attempt})")
                time.sleep(10)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= max_retries:
                    resp.raise_for_status()
                attempt += 1
                print(f"      HTTP {resp.status_code} — waiting 10s (retry {attempt})")
                time.sleep(10)
                continue
            resp.raise_for_status()
            break
        payload = resp.json()["response"]
        if payload.get("status") != "ok":
            raise RuntimeError(f"Guardian API status={payload.get('status')!r}")
        total_pages = payload.get("pages", 1)
        results.extend(payload.get("results", []))
        page += 1
        time.sleep(sleep_seconds)  # free tier: 1 request/second
    return results


def _wrap_html(fragment: str) -> bytes:
    """The API's ``body`` field is an HTML *fragment*; trafilatura wants a document."""
    return gzip.compress(
        f"<html><body><article>{fragment}</article></body></html>".encode("utf-8")
    )


def _insert(conn, cache_conn, articles: list[dict]) -> tuple[int, int]:
    """Insert metadata rows + cache body HTML. Returns (new_articles, cached_bodies)."""
    now = datetime.now(timezone.utc).isoformat()
    meta, blobs = [], []
    for a in articles:
        url = a.get("webUrl")
        fields = a.get("fields") or {}
        if not url:
            continue
        meta.append(
            (
                fields.get("headline") or a.get("webTitle"),
                url,
                DOMAIN,
                a.get("webPublicationDate"),  # already ISO-8601 UTC
                "English",
            )
        )
        body = fields.get("body")
        if body:
            blobs.append((url, _wrap_html(body), 200, now))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO articles "
        "(title, url, domain, published_at, language, tier) "
        "VALUES (?, ?, ?, ?, ?, 'reliable')",
        meta,
    )
    added = conn.total_changes - before
    cache_conn.executemany(
        "INSERT OR REPLACE INTO raw_html (url, html_gz, status_code, fetched_at) "
        "VALUES (?, ?, ?, ?)",
        blobs,
    )
    return added, len(blobs)


def ingest(
    config_path: str | Path = DEFAULT_CONFIG,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    cache_path: str | Path = db.DEFAULT_CACHE_PATH,
) -> int:
    """Run the Guardian ingest over the config's date range. Returns new-article count."""
    cfg = load_config(config_path)
    g = cfg.get("guardian", {})
    section = g.get("section", "football")
    page_size = int(g.get("page_size", 50))
    sleep_seconds = float(g.get("sleep_seconds", 1.0))
    start = datetime.strptime(cfg["date_range"]["start"], "%Y-%m-%d")
    end = datetime.strptime(cfg["date_range"]["end"], "%Y-%m-%d")
    api_key = _api_key()
    ledger_key = _LEDGER_PREFIX + section

    db.init_corpus(db_path)
    db.init_cache(cache_path)
    conn = db.connect(db_path)
    cache_conn = db.connect(cache_path)
    total_new = total_bodies = skipped = 0
    try:
        with httpx.Client() as client:
            current = start
            while current < end:
                window_end = min(current + timedelta(days=28), end)
                w_start, w_end = str(current.date()), str(window_end.date())

                done = conn.execute(
                    "SELECT 1 FROM ingest_log WHERE query=? AND window_start=? "
                    "AND window_end=?",
                    (ledger_key, w_start, w_end),
                ).fetchone()
                if done:
                    skipped += 1
                    current = window_end
                    continue

                articles = search_window(
                    client, section, current, window_end, api_key,
                    page_size=page_size, sleep_seconds=sleep_seconds,
                )
                added, bodies = _insert(conn, cache_conn, articles)
                conn.execute(
                    "INSERT OR REPLACE INTO ingest_log "
                    "(query, window_start, window_end, fetched_at, n_articles) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ledger_key, w_start, w_end,
                     datetime.now(timezone.utc).isoformat(), len(articles)),
                )
                cache_conn.commit()
                conn.commit()  # commit order: cache first — see sweep_query's rationale
                total_new += added
                total_bodies += bodies
                print(f"  {w_start} -> {w_end}: {len(articles)} articles, {added} new")
                current = window_end
    finally:
        conn.close()
        cache_conn.close()

    if skipped:
        print(f"resumed: skipped {skipped} window(s) already ingested")
    print(f"Done. {total_new} new articles, {total_bodies} bodies cached.")
    print("Run `corpus extract` to turn the cached bodies into body_full.")
    return total_new
