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

import json
import re
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


# GDELT sometimes emits invalid JSON: an unescaped backslash inside a headline (a real
# example: an Arabic title containing "الدوري 2024 \ 2025") and raw control characters.
# A strict parse raises on those, and swallowing the error would silently discard a whole
# WEEK of articles — indistinguishable from a quiet news week. So repair the two known
# defects, and only then give up (loudly).
_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def parse_articles(text: str) -> list[dict]:
    """Parse a GDELT response, repairing its known JSON defects.

    Raises ValueError when the payload is genuinely unparseable (e.g. a plain-text
    throttle notice), so the caller records the window as a *reported* gap instead of
    mistaking it for "no articles that week".
    """
    try:
        return json.loads(text).get("articles", [])
    except ValueError:
        repaired = _INVALID_ESCAPE.sub(r"\\\\", text)
        return json.loads(repaired, strict=False).get("articles", [])


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
    max_retries: int = 1,
    initial_backoff: float = 60.0,
) -> list[dict]:
    """One GDELT DOC query over a [start, end) window. Returns the article list.

    Retries once with backoff on 429/5xx, honouring `Retry-After`. Deliberately only
    *once*: retrying is right for a transient blip, but under a sustained throttle every
    retry multiplies the load that caused the throttle in the first place. The legacy
    notebook simply skipped a failed window and moved on, and that was gentler. The
    circuit breaker in `sweep_query` handles the sustained case.
    """
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": max_records,
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "format": "json",
    }
    backoff = initial_backoff
    for attempt in range(max_retries + 1):
        resp = client.get(GDELT_URL, params=params, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == max_retries:
                resp.raise_for_status()
            wait = float(resp.headers.get("Retry-After") or backoff)
            print(
                f"      HTTP {resp.status_code} — backing off {wait:.0f}s "
                f"(retry {attempt + 1}/{max_retries})"
            )
            time.sleep(wait)
            backoff *= 2
            continue
        resp.raise_for_status()
        if not resp.text.strip():
            return []
        # Deliberately NOT wrapped in try/except: an unparseable payload must propagate
        # so `download_range` records and reports the lost window.
        return parse_articles(resp.text)
    return []


class Throttled(RuntimeError):
    """GDELT is persistently rejecting us — stop the run instead of digging deeper.

    Every extra request while throttled extends the penalty, so the correct response to
    repeated rejections is to *leave*. The ingest ledger makes this cheap: whatever
    windows completed are saved, and the next run resumes from there.
    """


def _count_log(db_path) -> int:
    """How many windows are already banked in the ledger."""
    conn = db.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0]
    finally:
        conn.close()


def _window_done(conn, query: str, w_start: str, w_end: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM ingest_log WHERE query=? AND window_start=? AND window_end=?",
            (query, w_start, w_end),
        ).fetchone()
        is not None
    )


def _log_window(conn, query: str, w_start: str, w_end: str, n_articles: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ingest_log "
        "(query, window_start, window_end, fetched_at, n_articles) VALUES (?, ?, ?, ?, ?)",
        (query, w_start, w_end, datetime.now(timezone.utc).isoformat(), n_articles),
    )


def sweep_query(
    client: httpx.Client,
    conn,
    query: str,
    start: datetime,
    end: datetime,
    max_records: int,
    sleep_seconds: float,
    language: str | None,
    allowlist: set[str] | None,
    max_consecutive_failures: int = 3,
) -> tuple[int, Counter, list[tuple[str, str, str]], int]:
    """Sweep one query week by week, inserting and logging **per window**.

    Progress is durable at window granularity: interrupt the run at any point and the
    completed windows are already saved, so the next run resumes instead of re-requesting
    them. This matters because GDELT rate-limits hard — an all-or-nothing ingest re-hits
    windows it already has and trips the limiter again.

    Order is insert-then-log on purpose: if the process dies between the two, the worst
    case is one redundant re-fetch (inserts are idempotent), never lost articles.

    Returns (articles_added, rejected_by_allowlist, failed_windows, windows_skipped).
    """
    added_total = 0
    rejected_total: Counter = Counter()
    failed: list[tuple[str, str, str]] = []
    skipped = 0
    consecutive_failures = 0
    current = start
    while current < end:
        window_end = min(current + timedelta(days=7), end)
        w_start, w_end = str(current.date()), str(window_end.date())

        if _window_done(conn, query, w_start, w_end):
            skipped += 1
            current = window_end
            continue

        try:
            articles = search_gdelt(client, query, current, window_end, max_records)
        except Exception as exc:  # noqa: BLE001 — record the gap and keep sweeping
            print(f"    {w_start} -> {w_end}: FAILED {str(exc)[:60]}")
            failed.append((w_start, w_end, query))
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                raise Throttled(
                    f"{consecutive_failures} windows failed in a row — GDELT is "
                    f"throttling us. Stopping so we don't extend the penalty."
                )
            current = window_end
            time.sleep(sleep_seconds)
            continue

        consecutive_failures = 0  # a success clears the breaker
        added, rejected = _insert(conn, articles, language, allowlist)
        _log_window(conn, query, w_start, w_end, len(articles))
        conn.commit()
        added_total += added
        rejected_total.update(rejected)
        current = window_end
        time.sleep(sleep_seconds)

    return added_total, rejected_total, failed, skipped


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
    total_skipped = 0
    all_rejected: Counter = Counter()
    all_failed: list[tuple[str, str, str]] = []
    throttled = False
    try:
        with httpx.Client(headers={"User-Agent": _USER_AGENT}) as client:
            for query in queries:
                print(f"query: {query!r}")
                try:
                    added, rejected, failed, skipped = sweep_query(
                        client, conn, query, start, end, max_records, sleep_seconds,
                        language, allowlist,
                    )
                except Throttled as exc:
                    print(f"\n⛔ {exc}")
                    throttled = True
                    break
                all_failed.extend(failed)
                all_rejected.update(rejected)
                total_new += added
                total_skipped += skipped
                note = f", {skipped} window(s) already done" if skipped else ""
                print(f"  {added} new, {sum(rejected.values())} off-allowlist{note}")
    finally:
        conn.close()

    if throttled:
        done = _count_log(db_path)
        print(
            f"\nStopped early to avoid deepening the throttle.\n"
            f"  {done} window(s) are saved in the ingest ledger — they will be skipped\n"
            f"  next time. Wait a few hours, then re-run `corpus ingest` to continue\n"
            f"  from here. Nothing is lost."
        )

    if total_skipped:
        print(f"\nresumed: skipped {total_skipped} window(s) already ingested")

    print(f"Done. {total_new} new articles added.")
    if all_failed:
        print(
            f"\n⚠ {len(all_failed)} week-window(s) FAILED after retries — that is missing "
            f"articles, i.e. holes in temporal coverage:"
        )
        for win_start, win_end, query in all_failed[:15]:
            print(f"    {win_start} -> {win_end}  {query!r}")
        if len(all_failed) > 15:
            print(f"    ... and {len(all_failed) - 15} more")
        print(
            "  Re-run `corpus ingest` to retry them (inserts are idempotent), ideally "
            "with a larger `sleep_seconds`."
        )
    if all_rejected:
        print(f"rejected {sum(all_rejected.values())} articles from "
              f"{len(all_rejected)} off-allowlist domains. Top 10:")
        for domain, count in all_rejected.most_common(10):
            print(f"  {count:>5}  {domain}")
    return total_new
