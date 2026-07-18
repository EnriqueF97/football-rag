"""GDELT headline ingest.

Pulls article *metadata* (title, url, domain, published_at, language) from the GDELT
DOC 2.0 API into the ``articles`` table. Bodies are left NULL here — they are fetched
and extracted later (``fetch.py``, ``extract.py``).

Incremental by construction: ``INSERT OR IGNORE`` on the UNIQUE ``url`` means URLs
already in the DB are skipped, so re-running appends only genuinely new headlines. The
domain is a config knob — the query list in ``config/football_queries.yaml`` — so
retargeting the whole project is editing that file, not this code.

GDELT is an open, key-less API. It caps a request at 250 articles, so we sweep the
date range one week at a time per query to stay under that cap and under rate limits.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

from newsrag.corpus import db

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_CONFIG = Path("config/football_queries.yaml")

# GDELT blocks requests that identify as Python; present a browser User-Agent instead.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(Path(path)) as f:
        return yaml.safe_load(f)


def _normalize_domain(domain: str | None) -> str:
    d = (domain or "").strip().lower()
    return d[4:] if d.startswith("www.") else d


def domain_allowed(domain: str | None, allowlist: set[str] | None) -> bool:
    """Suffix- and www-insensitive allowlist check.

    ``bbc.co.uk`` in the list also admits ``sport.bbc.co.uk``. A ``None`` or empty
    allowlist means "allow everything" (the filter is disabled).
    """
    if not allowlist:
        return True
    d = _normalize_domain(domain)
    if not d:
        return False
    return any(d == allowed or d.endswith("." + allowed) for allowed in allowlist)


def load_allowlist(cfg: dict) -> set[str] | None:
    """Read `sources:` from the config. Returns None when the filter is disabled."""
    sources = cfg.get("sources") or {}
    if not sources.get("enabled", False):
        return None
    return {_normalize_domain(d) for d in (sources.get("allowlist") or [])} or None


def search_gdelt(
    client: httpx.Client,
    query: str,
    start: datetime,
    end: datetime,
    max_records: int = 250,
) -> list[dict]:
    """One GDELT DOC query over a [start, end) window. Returns the article list."""
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": max_records,
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "format": "json",
    }
    resp = client.get(GDELT_URL, params=params, timeout=30)
    resp.raise_for_status()
    if not resp.text.strip():
        return []
    try:
        return resp.json().get("articles", [])
    except ValueError:
        # GDELT sometimes returns a plain-text throttle notice instead of JSON.
        return []


def download_range(
    client: httpx.Client,
    query: str,
    start: datetime,
    end: datetime,
    max_records: int,
    sleep_seconds: float,
) -> list[dict]:
    """Sweep [start, end) week by week for one query, sleeping between requests."""
    rows: list[dict] = []
    current = start
    while current < end:
        week_end = min(current + timedelta(days=7), end)
        try:
            rows.extend(search_gdelt(client, query, current, week_end, max_records))
        except Exception as exc:  # noqa: BLE001 — log and continue, one bad week is fine
            print(f"    {current.date()} -> {week_end.date()}: ERROR {str(exc)[:60]}")
        current = week_end
        time.sleep(sleep_seconds)
    return rows


def _parse_seendate(seendate: str) -> str | None:
    """GDELT 'seendate' (e.g. '20250131T124500Z') -> ISO-8601 UTC string."""
    try:
        dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def _insert(
    conn,
    articles: list[dict],
    language: str | None,
    allowlist: set[str] | None = None,
) -> tuple[int, Counter]:
    """Insert new metadata rows (dedup by url, filtered by allowlist).

    Returns (rows_added, rejected_domain_counter) — the rejections are reported so we
    keep visibility into what the allowlist is excluding.
    """
    to_add = []
    rejected: Counter = Counter()
    for a in articles:
        if language and a.get("language") != language:
            continue
        url = a.get("url")
        if not url:
            continue
        domain = a.get("domain")
        if not domain_allowed(domain, allowlist):
            rejected[_normalize_domain(domain)] += 1
            continue
        to_add.append(
            (a.get("title"), url, domain, _parse_seendate(a.get("seendate")),
             a.get("language"))
        )
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO articles (title, url, domain, published_at, language) "
        "VALUES (?, ?, ?, ?, ?)",
        to_add,
    )
    conn.commit()
    return conn.total_changes - before, rejected


def ingest(
    config_path: str | Path = DEFAULT_CONFIG,
    db_path: str | Path = db.DEFAULT_DB_PATH,
) -> int:
    """Run the full ingest defined by the config. Returns the count of new articles."""
    cfg = load_config(config_path)
    start = datetime.strptime(cfg["date_range"]["start"], "%Y-%m-%d")
    end = datetime.strptime(cfg["date_range"]["end"], "%Y-%m-%d")
    g = cfg.get("gdelt", {})
    max_records = int(g.get("max_records", 250))
    sleep_seconds = float(g.get("sleep_seconds", 12))
    language = g.get("language", "English")
    queries = cfg["queries"]
    allowlist = load_allowlist(cfg)
    if allowlist:
        print(f"source allowlist: {len(allowlist)} domains")

    db.init_corpus(db_path)  # ensure the schema exists
    conn = db.connect(db_path)
    total_new = 0
    all_rejected: Counter = Counter()
    try:
        with httpx.Client(headers={"User-Agent": _USER_AGENT}) as client:
            for query in queries:
                print(f"query: {query!r}")
                rows = download_range(
                    client, query, start, end, max_records, sleep_seconds
                )
                added, rejected = _insert(conn, rows, language, allowlist)
                all_rejected.update(rejected)
                total_new += added
                print(f"  {len(rows)} fetched, {added} new, {sum(rejected.values())} off-allowlist")
    finally:
        conn.close()

    print(f"Done. {total_new} new articles added.")
    if all_rejected:
        print(f"rejected {sum(all_rejected.values())} articles from "
              f"{len(all_rejected)} off-allowlist domains. Top 10:")
        for domain, count in all_rejected.most_common(10):
            print(f"  {count:>5}  {domain}")
    return total_new
