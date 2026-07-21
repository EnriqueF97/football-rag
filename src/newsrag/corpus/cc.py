"""Common Crawl ingest — the tabloid tier (Phase 1.8).

Two stages, mirroring the ingest→fetch split of the rest of the pipeline:

* **Discovery** (``corpus ingest-cc``): query the Common Crawl CDX index for each
  configured URL pattern × monthly crawl, and insert article metadata into ``articles``
  plus a WARC pointer (file, offset, length) into ``cc_captures`` in the cache DB.
* **Bodies** (``corpus fetch-cc``): download each capture as a **byte-range** from
  ``data.commoncrawl.org`` and store the HTML in the ``raw_html`` cache, exactly as if
  ``fetch.py`` had downloaded it. ``extract.py`` then processes it unchanged.

Why Common Crawl at all: the tabloid tier (mirror, 90min, teamtalk, caughtoffside, …)
blocks our fetcher with Cloudflare 403s, but lets CCBot in — so the HTML already sits
in CC's public archive and the publisher is never contacted. The reliable tier
(Guardian, BBC, Sky, ESPN) blocks CCBot instead, which is why this module is *only*
the tabloid tier; the Guardian arrives via its API (``guardian.py``).

The CDX index is operationally flaky (intermittent 502/504 under load — observed
repeatedly while building this). The retry policy is therefore patient by design:
several attempts with long waits, and the ``ingest_log`` ledger records each completed
(pattern × crawl) so an aborted run resumes instead of re-asking.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from newsrag.corpus import db

COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
CDX_URL = "https://index.commoncrawl.org/{crawl}-index"
DATA_URL = "https://data.commoncrawl.org/"
DEFAULT_CONFIG = Path("config/football_queries.yaml")

_USER_AGENT = "newsrag-corpus/0.1 (personal research project; contact via github)"

# URL substrings that mark index/nav/utility pages, not articles. Kept short and
# high-precision — the validity filter in extract.py is the real gate; this only
# avoids wasting byte-range fetches on obvious non-articles.
_NON_ARTICLE = (
    "/robots.txt", "/tag/", "/tags/", "/category/", "/author/", "/page/",
    "/search", "/newsletter", "/rss", "/feed", "/sitemap", "/video/",
    "/betting/", "/preference", "/privacy", "/cookie", "/about-us",
    # Archive/date index pages (e.g. fourfourtwo.com "All content Archive | Nov 2003"):
    # near-identical boilerplate that formed a 60-item dup cluster on the first run.
    "/all-content-archive", "/archive/", "/archives/",
)

_URL_DATE = re.compile(r"/(20\d{2})/(\d{2})/")

# Auto-generated per-player stat stubs (e.g. teamtalk.com/archie-gray/injury): a
# player slug followed by a stat keyword as the final path segment. Near-identical
# boilerplate, not journalism — the project is text-native, not stats. Anchored to the
# path end so real articles (…/injury-crisis-at-arsenal) are not caught.
_STAT_STUB = re.compile(
    r"/[a-z0-9-]+/(injury|results|fixtures|profile|stats|career|squad)/?$", re.I
)


def url_in_window(url: str, start: str, end: str) -> bool:
    """Cheap pre-fetch window check from the URL path date (YYYY-MM-DD bounds).

    Only decides when the URL *has* a /YYYY/MM/ path date — the big out-of-window
    offenders (caughtoffside, mirror, thisisanfield) all do, so this filters them at
    discovery for free. Slug-only URLs (90min) return True here and are window-checked
    post-fetch against the HTML's real published date instead.
    """
    m = _URL_DATE.search(url)
    if not m:
        return True
    ym = f"{m.group(1)}-{m.group(2)}"
    return start[:7] <= ym <= end[:7]


def load_cc_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(Path(path)) as f:
        cfg = yaml.safe_load(f)
    cc = cfg.get("commoncrawl") or {}
    if not cc.get("url_patterns"):
        raise SystemExit("config has no `commoncrawl.url_patterns` — nothing to ingest")
    return cfg


def _get_with_patience(
    client: httpx.Client, url: str, params: dict | None = None,
    max_retries: int = 5, backoff: float = 15.0,
) -> httpx.Response | None:
    """GET against the flaky CDX index: long, patient retries on 5xx/429/transport.

    Returns None when the resource genuinely has no captures (CDX signals that with
    404 + a JSON message), and raises after exhausting retries on server errors.
    """
    for attempt in range(max_retries + 1):
        try:
            resp = client.get(url, params=params, timeout=60)
        except httpx.TransportError as exc:
            if attempt == max_retries:
                raise
            print(f"      {type(exc).__name__} — waiting {backoff:.0f}s "
                  f"(retry {attempt + 1}/{max_retries})")
            time.sleep(backoff)
            continue
        if resp.status_code == 404:
            return None  # "No Captures found" — a real answer, not an error
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == max_retries:
                resp.raise_for_status()
            print(f"      HTTP {resp.status_code} — waiting {backoff:.0f}s "
                  f"(retry {attempt + 1}/{max_retries})")
            time.sleep(backoff)
            continue
        resp.raise_for_status()
        return resp
    return None


def list_crawls(client: httpx.Client, first_crawl: str) -> list[str]:
    """Crawl ids covering the project window (lexicographic works: zero-padded weeks)."""
    resp = _get_with_patience(client, COLLINFO_URL)
    ids = [c["id"] for c in resp.json()]
    return sorted(i for i in ids if i >= first_crawl)


def parse_cdx(text: str) -> list[dict]:
    """CDX json-lines → capture dicts. Tolerates the index's occasional junk lines."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line, strict=False)  # control chars appear in the wild
        except ValueError:
            continue
        if "url" in r and "filename" in r:
            out.append(r)
    return out


def looks_like_article(url: str) -> bool:
    p = urlparse(url)
    path = p.path or "/"
    if path == "/" or p.query:
        return False
    if _STAT_STUB.search(path):
        return False
    low = url.lower()
    return not any(marker in low for marker in _NON_ARTICLE)


def published_at_for(url: str, capture_ts: str) -> str:
    """Publication date: URL path date when present (exact), else capture time (upper
    bound — CC crawls news pages days-to-weeks after publication)."""
    m = _URL_DATE.search(url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01T00:00:00+00:00"
    try:
        dt = datetime.strptime(capture_ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return ""


def _window_done(conn, query: str, crawl: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM ingest_log WHERE query=? AND window_start=? AND window_end=?",
        (query, crawl, crawl),
    ).fetchone() is not None


def discover(
    config_path: str | Path = DEFAULT_CONFIG,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    cache_path: str | Path = db.DEFAULT_CACHE_PATH,
    max_crawls: int | None = None,
) -> int:
    """Stage 1: CDX index → `articles` metadata + `cc_captures` WARC pointers."""
    cfg = load_cc_config(config_path)
    cc = cfg["commoncrawl"]
    patterns: list[str] = cc["url_patterns"]
    first_crawl = cc.get("first_crawl", "CC-MAIN-2024-33")
    cap = int(cc.get("max_per_pattern_per_crawl", 250))
    sleep_s = float(cc.get("sleep_seconds", 4))
    win_start = cfg["date_range"]["start"]
    win_end = cfg["date_range"]["end"]
    from_ts = win_start.replace("-", "") + "000000"
    to_ts = win_end.replace("-", "") + "235959"

    db.init_corpus(db_path)
    db.init_cache(cache_path)
    conn = db.connect(db_path)
    cache_conn = db.connect(cache_path)
    total_new = 0
    try:
        with httpx.Client(headers={"User-Agent": _USER_AGENT}) as client:
            crawls = list_crawls(client, first_crawl)
            if max_crawls is not None:
                crawls = crawls[:max_crawls]
            print(f"{len(crawls)} crawls × {len(patterns)} patterns "
                  f"(cap {cap}/pair, ledger-resumable)")
            failed: list[tuple[str, str]] = []
            for crawl in crawls:
                for pattern in patterns:
                    ledger_key = f"cc:{pattern}"
                    if _window_done(conn, ledger_key, crawl):
                        continue
                    # A pattern×crawl that exhausts its retries is a recorded gap, not
                    # a dead run — it stays out of the ledger and retries next run.
                    try:
                        resp = _get_with_patience(
                            client, CDX_URL.format(crawl=crawl), params={
                                "url": pattern, "output": "json",
                                "filter": ["status:200", "mime-detected:text/html"],
                                "from": from_ts, "to": to_ts,
                                "limit": cap * 4,  # room for non-article/dup discard
                            },
                        )
                    except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                        print(f"  {crawl}  {pattern:<38} FAILED {str(exc)[:60]}")
                        failed.append((crawl, pattern))
                        time.sleep(sleep_s)
                        continue
                    captures = parse_cdx(resp.text) if resp is not None else []
                    added = _ingest_captures(
                        conn, cache_conn, captures, cap, win_start, win_end
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO ingest_log "
                        "(query, window_start, window_end, fetched_at, n_articles) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (ledger_key, crawl, crawl,
                         datetime.now(timezone.utc).isoformat(), len(captures)),
                    )
                    conn.commit()
                    cache_conn.commit()
                    total_new += added
                    print(f"  {crawl}  {pattern:<38} {len(captures):>4} captures, "
                          f"{added:>4} new")
                    time.sleep(sleep_s)
    finally:
        conn.close()
        cache_conn.close()
    print(f"Done. {total_new} new articles discovered. "
          f"Run `corpus fetch-cc` to download the bodies.")
    if failed:
        print(f"⚠ {len(failed)} pattern×crawl pair(s) failed after retries — "
              f"re-run `corpus ingest-cc` to retry them (the ledger skips the rest):")
        for crawl, pattern in failed[:10]:
            print(f"    {crawl}  {pattern}")
    return total_new


def _ingest_captures(
    conn, cache_conn, captures: list[dict], cap: int,
    win_start: str, win_end: str,
) -> int:
    """Insert up to `cap` new articles (+ WARC pointers), newest capture per URL.

    URL-path dates outside [win_start, win_end) are dropped here — CC's from/to filter
    is on *capture* time, so recently-crawled old archive pages slip through it.
    """
    by_url: dict[str, dict] = {}
    for c in captures:
        url = c["url"]
        if not looks_like_article(url) or not url_in_window(url, win_start, win_end):
            continue
        prev = by_url.get(url)
        if prev is None or c.get("timestamp", "") > prev.get("timestamp", ""):
            by_url[url] = c
    added = 0
    for url, c in by_url.items():
        if added >= cap:
            break
        domain = urlparse(url).netloc.removeprefix("www.")
        cur = conn.execute(
            "INSERT OR IGNORE INTO articles "
            "(title, url, domain, published_at, language, tier) "
            "VALUES (NULL, ?, ?, ?, 'English', 'tabloid')",
            (url, domain, published_at_for(url, c.get("timestamp", ""))),
        )
        if cur.rowcount:  # only count/point genuinely new URLs
            added += 1
            cache_conn.execute(
                "INSERT OR REPLACE INTO cc_captures "
                "(url, warc_filename, warc_offset, warc_length, crawl_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (url, c["filename"], int(c["offset"]), int(c["length"]),
                 c.get("timestamp", "")),
            )
    return added


# --- Stage 2: bodies via WARC byte-ranges ------------------------------------------


def _dechunk(payload: bytes) -> bytes:
    """Undo HTTP chunked transfer encoding (rare in CC records, but real)."""
    out, i = [], 0
    while i < len(payload):
        j = payload.find(b"\r\n", i)
        if j < 0:
            break
        try:
            size = int(payload[i:j].split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        out.append(payload[j + 2 : j + 2 + size])
        i = j + 2 + size + 2
    return b"".join(out) if out else payload


def warc_html(record_gz: bytes) -> bytes | None:
    """One gzipped WARC response record → the HTML payload (None if not a 200 page)."""
    try:
        raw = gzip.decompress(record_gz)
    except OSError:
        return None
    head, _, rest = raw.partition(b"\r\n\r\n")
    if b"WARC-Type: response" not in head:
        return None
    http_head, _, payload = rest.partition(b"\r\n\r\n")
    if b" 200" not in http_head.split(b"\r\n", 1)[0]:
        return None
    if b"chunked" in http_head.lower():
        payload = _dechunk(payload)
    return payload


_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)

# Publication date from the page's own metadata. Capture timestamps are only an upper
# bound — news sites keep old articles linked, so a 2017 story can be captured in a
# 2024 crawl; trusting the capture time would silently corrupt temporal coverage
# (verified: a ~2017 Lallana piece arrived via CC-MAIN-2024-33). Every news CMS emits
# one of these two forms.
_META_DATE = re.compile(
    rb'property=["\']article:published_time["\']\s+content=["\']([^"\']+)'
    rb'|content=["\']([^"\']+)["\']\s+property=["\']article:published_time["\']'
    rb'|"datePublished"\s*:\s*"([^"]+)"',
    re.I,
)


def _title_from(html: bytes) -> str | None:
    m = _TITLE.search(html[:200_000])
    if not m:
        return None
    import html as html_mod
    t = html_mod.unescape(m.group(1).decode("utf-8", errors="replace"))
    return re.sub(r"\s+", " ", t).strip()[:300] or None


def _published_from(html: bytes) -> str | None:
    m = _META_DATE.search(html[:300_000])
    if not m:
        return None
    raw = next(g for g in m.groups() if g).decode("utf-8", errors="replace").strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


async def _fetch_one(client, sem, url, filename, offset, length, pace: float):
    """Returns (url, html_or_None, last_status). CloudFront rate-limits with a plain
    403 ("Request blocked") — observed live after ~16k requests at concurrency 8 —
    so 403 is *retryable here*, with a long wait, unlike a publisher 403."""
    rng = f"bytes={offset}-{offset + length - 1}"
    status = None
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.get(
                    DATA_URL + filename, headers={"Range": rng}, timeout=60
                )
                status = resp.status_code
            except httpx.TransportError:
                await asyncio.sleep(10 * (attempt + 1))
                continue
            if status in (200, 206):
                await asyncio.sleep(pace)  # steady trickle beats a burst-then-ban
                return url, warc_html(resp.content), status
            if status in (403, 429) or status >= 500:
                await asyncio.sleep(30 * (attempt + 1))
                continue
            return url, None, status
    return url, None, status


class DataThrottled(RuntimeError):
    """data.commoncrawl.org is blocking us — stop instead of burning the work list."""


async def _fetch_all(rows, concurrency, pace):
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}) as client:
        tasks = [
            _fetch_one(client, sem, url, fn, off, ln, pace)
            for url, fn, off, ln in rows
        ]
        return [await coro for coro in asyncio.as_completed(tasks)]


def fetch_bodies(
    db_path: str | Path = db.DEFAULT_DB_PATH,
    cache_path: str | Path = db.DEFAULT_CACHE_PATH,
    concurrency: int = 3,
    limit: int | None = None,
    batch_size: int = 200,
    pace: float = 0.3,
) -> dict:
    """Stage 2: WARC byte-ranges → `raw_html` cache (+ backfill NULL titles).

    Commits per batch so an interrupted run keeps its progress; resumable because
    already-cached URLs are excluded from the work list. Defaults are deliberately
    gentle (~concurrency/pace ≈ a few req/s): a first harvest at concurrency 8 with
    no pacing earned a CloudFront IP block after ~2k of 16k fetches. If a batch
    comes back majority-403 anyway, `DataThrottled` aborts the run — waiting out the
    block costs less than deepening it (same lesson as GDELT, same day).
    """
    conn = db.connect(db_path)
    cache_conn = db.connect(cache_path)
    tally: Counter = Counter()
    try:
        todo = cache_conn.execute(
            "SELECT url, warc_filename, warc_offset, warc_length FROM cc_captures "
            "WHERE url NOT IN (SELECT url FROM raw_html)"
        ).fetchall()
        if limit is not None:
            todo = todo[:limit]
        print(f"to fetch from Common Crawl: {len(todo)}")
        now = datetime.now(timezone.utc).isoformat()
        for i in range(0, len(todo), batch_size):
            batch = todo[i : i + batch_size]
            results = asyncio.run(_fetch_all(batch, concurrency, pace))
            blocked = sum(1 for _, html, st in results if html is None and st == 403)
            if blocked > len(results) // 2:
                raise DataThrottled(
                    f"{blocked}/{len(results)} requests in this batch got 403 — "
                    f"data.commoncrawl.org is blocking this IP. Progress is saved; "
                    f"wait for the block to lift and re-run `corpus fetch-cc`."
                )
            for url, html, _status in results:
                if html is None:
                    tally["warc_failed"] += 1
                    continue
                tally["ok"] += 1
                cache_conn.execute(
                    "INSERT OR REPLACE INTO raw_html "
                    "(url, html_gz, status_code, fetched_at) VALUES (?, ?, 200, ?)",
                    (url, gzip.compress(html), now),
                )
                title = _title_from(html)
                if title:
                    conn.execute(
                        "UPDATE articles SET title = ? "
                        "WHERE url = ? AND title IS NULL", (title, url),
                    )
                published = _published_from(html)
                if published:  # page metadata beats any discovery-time approximation
                    conn.execute(
                        "UPDATE articles SET published_at = ? WHERE url = ?",
                        (published, url),
                    )
            cache_conn.commit()
            conn.commit()
            print(f"  {min(i + batch_size, len(todo))}/{len(todo)}  {dict(tally)}")
    finally:
        conn.close()
        cache_conn.close()
    print(f"Done. outcomes: {dict(tally)}")
    return dict(tally)
