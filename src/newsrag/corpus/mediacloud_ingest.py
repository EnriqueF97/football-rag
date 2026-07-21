"""Media Cloud ingest — reliable-tier diversification (Phase 1.9).

Discovers article metadata (url, title, publish_date, domain) for the reliable outlets
that Common Crawl can't reach — BBC, Sky Sports, ESPN — which block CCBot but serve
their HTML fine to a direct fetch. So this is *discovery only*: it fills `articles`
(tier='reliable'), and the existing `fetch.py` + `extract.py` get the bodies, exactly
as for any other fetched source. GDELT's replacement, minus GDELT's throttle.

Media Cloud's Online News Archive (200M+ stories) is searched via its Python client
(`mediacloud.api.SearchApi`), token from `MEDIACLOUD_API_KEY` in `.env`. Auth is a free
key (https://search.mediacloud.org → sign up → account → API token). The client enforces
its own polite rate limit, so this module adds no pacing of its own.

Resumable per (source × month) through the shared `ingest_log` ledger, mirroring
`guardian.py`: interrupt at any point and the completed months are banked, so a re-run
skips them. INSERT OR IGNORE on the UNIQUE url makes re-fetching a partial month cheap.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from pathlib import Path

import yaml

from newsrag.corpus import db

DEFAULT_CONFIG = Path("config/football_queries.yaml")


def _story_list_with_retry(search, *args, max_retries: int = 5, backoff: float = 20.0,
                           **kwargs):
    """Call story_list, retrying transient failures over a long run. Two kinds seen live:
    a `requests` connection reset (network blip), and Media Cloud's own
    `APIResponseError` for server-side 5xx (e.g. a Cloudflare 521 while their backend
    restarts). 4xx (auth/bad-request) is *not* retried — it won't fix itself. The ledger
    already makes a crash cheap; this just avoids losing an in-progress month to a blip.
    """
    import requests
    from mediacloud.error import APIResponseError

    attempt = 0
    while True:
        try:
            return search.story_list(*args, **kwargs)
        except APIResponseError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", 0)
            if status < 500 or attempt >= max_retries:
                raise  # client error (4xx) or out of retries — surface it
            attempt += 1
            print(f"    API {status} — waiting {backoff:.0f}s "
                  f"(retry {attempt}/{max_retries})")
            time.sleep(backoff)
        except requests.exceptions.RequestException as exc:
            if attempt >= max_retries:
                raise
            attempt += 1
            print(f"    {type(exc).__name__} — waiting {backoff:.0f}s "
                  f"(retry {attempt}/{max_retries})")
            time.sleep(backoff)


def load_mc_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    with open(Path(path)) as f:
        cfg = yaml.safe_load(f)
    if not (cfg.get("mediacloud") or {}).get("sources"):
        raise SystemExit("config has no `mediacloud.sources` — nothing to ingest")
    return cfg


def _month_windows(start: dt.date, end: dt.date):
    """Yield (win_start, win_end) month-aligned [start, end) sub-windows."""
    cur = start
    while cur < end:
        if cur.month == 12:
            nxt = dt.date(cur.year + 1, 1, 1)
        else:
            nxt = dt.date(cur.year, cur.month + 1, 1)
        yield cur, min(nxt, end)
        cur = nxt


def resolve_source_id(directory, domain: str) -> int | None:
    """Domain → Media Cloud numeric source id (None if the archive doesn't know it)."""
    page = directory.source_list(name=domain)
    for src in page.get("results", []):
        # source_list matches by substring; require the exact registered domain
        if src.get("name") == domain:
            return src["id"]
    # fall back to the first result if no exact hit (Media Cloud normalises some names)
    results = page.get("results", [])
    return results[0]["id"] if results else None


def _window_done(conn, key: str, w_start: str, w_end: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM ingest_log WHERE query=? AND window_start=? AND window_end=?",
        (key, w_start, w_end),
    ).fetchone() is not None


def _is_soccer_url(url: str) -> bool:
    """These outlets are multi-sport and 'football' is ambiguous (US 'football' = NFL:
    the query pulls ESPN NFL/college-football coverage). Soccer lives under a stable path
    segment — /football/ (BBC, Sky; UK usage) or /soccer/ (ESPN; US usage) — so gate on
    that. Precision over recall: some football at generic /sport/articles/ paths is
    dropped, but no other-sport content leaks into a soccer corpus.
    """
    low = url.lower()
    return "/football/" in low or "/soccer/" in low


def _insert_stories(conn, stories: list[dict], domain: str) -> int:
    rows = []
    for s in stories:
        url = s.get("url")
        if not url:
            continue
        if not _is_soccer_url(url):
            continue
        # BBC/ESPN publish foreign-language editions (BBC Hausa, ESPN Deportes...) that
        # match a loose query. Drop them when the language is known and not English;
        # keep unknown-language rows and let extract.py's validity filter decide.
        lang = s.get("language")
        if lang and lang != "en":
            continue
        pub = s.get("publish_date")
        published_at = (
            dt.datetime(pub.year, pub.month, pub.day, tzinfo=dt.timezone.utc).isoformat()
            if isinstance(pub, dt.date) else None
        )
        rows.append((s.get("title"), url, domain, published_at, s.get("language")))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO articles "
        "(title, url, domain, published_at, language, tier) "
        "VALUES (?, ?, ?, ?, ?, 'reliable')",
        rows,
    )
    return conn.total_changes - before


def ingest(
    config_path: str | Path = DEFAULT_CONFIG,
    db_path: str | Path = db.DEFAULT_DB_PATH,
) -> int:
    """Discover reliable-tier articles via Media Cloud into `articles` (metadata only)."""
    import mediacloud.api as mc_api  # local import: optional dep, only needed here

    token = os.environ.get("MEDIACLOUD_API_KEY")
    if not token:
        raise SystemExit(
            "MEDIACLOUD_API_KEY not set. Get a free key at https://search.mediacloud.org "
            "(sign up → account → API token), add it to .env, and run via "
            "`make ingest-mediacloud`."
        )

    cfg = load_mc_config(config_path)
    mc = cfg["mediacloud"]
    domains: list[str] = mc["sources"]
    query: str = mc.get("query", "football OR soccer")
    page_size = int(mc.get("page_size", 1000))
    max_per_month = int(mc.get("max_per_month", 500))
    start = dt.date.fromisoformat(cfg["date_range"]["start"])
    end = dt.date.fromisoformat(cfg["date_range"]["end"])

    search = mc_api.SearchApi(token)
    directory = mc_api.DirectoryApi(token)

    db.init_corpus(db_path)
    conn = db.connect(db_path)
    total_new = 0
    try:
        for domain in domains:
            source_id = resolve_source_id(directory, domain)
            if source_id is None:
                print(f"⚠ {domain}: not found in Media Cloud — skipping")
                continue
            key = f"mediacloud:{domain}"
            print(f"source: {domain} (id={source_id})")
            for w_start, w_end in _month_windows(start, end):
                ws, we = w_start.isoformat(), w_end.isoformat()
                if _window_done(conn, key, ws, we):
                    continue
                added_window = 0
                fetched = 0
                token_page = None
                while True:
                    stories, token_page = _story_list_with_retry(
                        search, query, w_start, w_end, source_ids=[source_id],
                        page_size=page_size, pagination_token=token_page,
                    )
                    # Cap per month so no single source dominates the corpus (balance,
                    # not volume, is the goal). Truncate the last page to the cap.
                    room = max_per_month - fetched
                    if len(stories) > room:
                        stories = stories[:room]
                        token_page = None
                    fetched += len(stories)
                    added_window += _insert_stories(conn, stories, domain)
                    if not token_page:
                        break
                conn.execute(
                    "INSERT OR REPLACE INTO ingest_log "
                    "(query, window_start, window_end, fetched_at, n_articles) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (key, ws, we, dt.datetime.now(dt.timezone.utc).isoformat(), fetched),
                )
                conn.commit()
                total_new += added_window
                print(f"  {ws} -> {we}: {fetched} stories, {added_window} new")
    finally:
        conn.close()
    print(f"Done. {total_new} new reliable-tier articles discovered.")
    print("Run `make fetch && make extract` to download and extract their bodies.")
    return total_new
