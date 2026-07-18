"""Async article-HTML fetcher with an on-disk cache and robots.txt politeness.

Downloads the raw HTML for articles that don't have it cached yet and stores it
gzip-compressed in the HTML cache DB (``data/cache/html.sqlite``). Body extraction is a
separate step (``extract.py``) that reads this cache, so re-extracting never re-downloads.

Politeness:

* ``robots.txt`` is honoured per domain (fetched once, cached). Disallowed URLs are
  skipped. Respecting it isn't technically enforced — doing it anyway is the signal.
* Concurrency is capped **per domain** (default 3) *and* globally (default 15), because
  courtesy is measured per host, not in aggregate.

Resume: URLs already in the cache are skipped, so re-running only fetches what's missing.
"""

from __future__ import annotations

import asyncio
import gzip
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from newsrag.corpus import db

# A browser-like UA so news sites serve us the same HTML a reader sees (many block
# obvious bots). We still honour robots.txt — politeness is about behaviour, not identity.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _domain(url: str) -> str:
    return urlparse(url).netloc


async def _get_robots(client: httpx.AsyncClient, domain: str, cache: dict) -> RobotFileParser:
    """Fetch + parse a domain's robots.txt once, then cache it. Lenient on failure."""
    if domain in cache:
        return cache[domain]
    rp = RobotFileParser()
    try:
        resp = await client.get(f"https://{domain}/robots.txt", timeout=10)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.allow_all = True  # no robots file (or error) → treat as allowed
    except Exception:
        rp.allow_all = True
    cache[domain] = rp
    return rp


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    per_domain_sems: dict,
    global_sem: asyncio.Semaphore,
    robots_cache: dict,
    respect_robots: bool,
) -> tuple[str, bytes | None, int | None, str]:
    """Fetch one URL. Returns (url, gzipped_html_or_None, status_code, outcome)."""
    domain = _domain(url)
    if respect_robots:
        rp = await _get_robots(client, domain, robots_cache)
        if not rp.can_fetch(_USER_AGENT, url):
            return (url, None, None, "robots_blocked")

    async with global_sem, per_domain_sems[domain]:
        try:
            resp = await client.get(url, timeout=15, follow_redirects=True)
            # Cache the response either way (so a persistent 403 isn't re-hit every run),
            # but label the outcome by HTTP status. Only 2xx is real content; extract.py
            # gates on status 200 so block/error pages never become article bodies.
            outcome = "ok" if 200 <= resp.status_code < 300 else f"http_{resp.status_code}"
            return (url, gzip.compress(resp.content), resp.status_code, outcome)
        except Exception as exc:  # noqa: BLE001 — record and move on; retried next run
            return (url, None, None, f"error:{type(exc).__name__}")


async def _run(urls: list[str], per_domain: int, global_cap: int, respect_robots: bool):
    # Semaphores are created lazily inside the running loop (required for asyncio).
    per_domain_sems: dict = defaultdict(lambda: asyncio.Semaphore(per_domain))
    global_sem = asyncio.Semaphore(global_cap)
    robots_cache: dict = {}
    results = []
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}) as client:
        tasks = [
            _fetch_one(client, url, per_domain_sems, global_sem, robots_cache, respect_robots)
            for url in urls
        ]
        for coro in asyncio.as_completed(tasks):
            results.append(await coro)
    return results


def fetch(
    db_path=db.DEFAULT_DB_PATH,
    cache_path=db.DEFAULT_CACHE_PATH,
    per_domain: int = 3,
    max_concurrency: int = 15,
    respect_robots: bool = True,
    limit: int | None = None,
) -> dict:
    """Download HTML for un-cached articles into the cache DB. Returns an outcome tally."""
    db.init_cache(cache_path)
    conn = db.connect(db_path)
    cache_conn = db.connect(cache_path)
    try:
        cached = {u for (u,) in cache_conn.execute("SELECT url FROM raw_html")}
        pending = [
            u for (u,) in conn.execute("SELECT url FROM articles WHERE body_full IS NULL")
        ]
        todo = [u for u in pending if u not in cached]
        if limit is not None:
            todo = todo[:limit]
        print(f"to fetch: {len(todo)}  (skipping {len(pending) - len(todo)} already cached)")
        if not todo:
            return {}

        results = asyncio.run(_run(todo, per_domain, max_concurrency, respect_robots))

        # Cache 2xx (real content) and *permanent* client errors (avoid re-hitting a
        # site that blocks us). Transient errors (429, 5xx) are left uncached so they
        # retry on the next run.
        now = datetime.now(timezone.utc).isoformat()
        _permanent = {401, 403, 404, 410}
        to_store = [
            (url, blob, status, now)
            for (url, blob, status, outcome) in results
            if blob is not None and (outcome == "ok" or status in _permanent)
        ]
        cache_conn.executemany(
            "INSERT OR REPLACE INTO raw_html (url, html_gz, status_code, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            to_store,
        )
        cache_conn.commit()
    finally:
        conn.close()
        cache_conn.close()

    tally = Counter(outcome for (_, _, _, outcome) in results)
    print(f"stored: {len(to_store)}  |  outcomes: {dict(tally)}")
    return dict(tally)
