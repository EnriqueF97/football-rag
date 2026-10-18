"""Drafting the golden set: choosing which documents to write questions from.

Questions are written **from** the corpus, never from memory — that is what makes every
item answerable by construction, and `GoldenItem.source_doc_id` is the audit trail for the
claim. So the first problem is not "what should I ask?" but "which documents do I read?".

Why that needs care rather than `ORDER BY RANDOM()`:

    theguardian.com   10,584   42%
    bbc.co.uk          7,845   31%

Two domains are 73% of the usable corpus. A uniform random sample inherits that shape, and
a golden set written from it cannot answer "does retrieval behave differently per source?"
— the question is unaskable because the evidence was never collected. The same argument
holds for time: a set drawn mostly from three busy months measures those months.

So we stratify over **(month, domain)** — 138 buckets across 23 months — and take round
robin across them. That deliberately *over*-samples small outlets relative to their corpus
share. That is the point: the golden set is a measuring instrument, not a representative
sample of the news.

Sampling is **seeded and reproducible**. Re-running with the same seed must return the same
documents, or the set cannot be rebuilt and "which documents did this come from?" has no
answer.

`sample_docs` deliberately spreads documents apart, which is right for single-document
questions and useless for `multi_doc` and `contradictory` ones: those need several outlets
covering **the same story**. `sample_bundles` does the opposite job — it groups documents
by (club named in the headline, short time block) and keeps groups covered by several
domains. Measured 2026-10-18: 1,040 (day, club) groups reach ≥3 outlets, 977 of them mixing
reliable and tabloid tiers — the raw material for contradictions.

Known bias, to be stated in the README: grouping is lexical (a club name in the title), so
bundle-derived questions favour documents that share vocabulary — i.e. BM25. Round 2 of
relevance pooling, with dense retrieval, is what corrects for it.
"""

from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from newsrag.corpus.smell import _USABLE
from newsrag.eval.golden import DEFAULT_PATH

#: Below this, a body is a stub or a teaser — not enough prose to support a real question.
#: 23,128 of the 25,215 usable documents clear this bar, so it costs us almost nothing.
MIN_BODY_CHARS = 1500

#: Headline spellings -> one canonical club, so "Man Utd" and "Manchester United" share a
#: bundle. Covers every Premier League club across both corpus seasons (2024-25, 2025-26).
#: Deliberately absent: "Villa", "Palace", "Forest", "Manchester" — each also means
#: something else in a football headline (David Villa, Villa Park, "City"/"United").
CLUB_ALIASES = {
    "Arsenal": "Arsenal",
    "Aston Villa": "Aston Villa",
    "Bournemouth": "Bournemouth",
    "Brentford": "Brentford",
    "Brighton": "Brighton",
    "Burnley": "Burnley",
    "Chelsea": "Chelsea",
    "Crystal Palace": "Crystal Palace",
    "Everton": "Everton",
    "Fulham": "Fulham",
    "Ipswich": "Ipswich",
    "Leeds": "Leeds",
    "Leicester": "Leicester",
    "Liverpool": "Liverpool",
    "Manchester City": "Manchester City",
    "Man City": "Manchester City",
    "Manchester United": "Manchester United",
    "Man Utd": "Manchester United",
    "Man United": "Manchester United",
    "Newcastle": "Newcastle",
    "Nottingham Forest": "Nottingham Forest",
    "Nott'm Forest": "Nottingham Forest",
    "Southampton": "Southampton",
    "Sunderland": "Sunderland",
    "Tottenham": "Tottenham",
    "Spurs": "Tottenham",
    "West Ham": "West Ham",
    "Wolves": "Wolves",
    "Wolverhampton": "Wolves",
}

# Longest alias first, so "Manchester United" wins over a shorter overlapping alias.
# Word boundaries stop "Leeds" matching inside another word; case-sensitive so the verb
# "spurs" in "Arteta spurs his players" is not read as Tottenham.
_CLUB_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in sorted(CLUB_ALIASES, key=len, reverse=True)) + r")\b"
)


def _clubs_in(title: str | None) -> set[str]:
    """Canonical clubs named in a headline. (given — regex plumbing, nothing to learn)

    Expected:
        "Arsenal predicted lineup vs Liverpool" -> {"Arsenal", "Liverpool"}
        "Man Utd eye Spurs midfielder"           -> {"Manchester United", "Tottenham"}
        "Champions League draw explained"         -> set()
    """
    return {CLUB_ALIASES[m] for m in _CLUB_RE.findall(title or "")}


def get_docs(conn, doc_ids: list[int]) -> list[dict]:
    """Full rows for the given ids, in the order given. (given)

    The second step of reading a bundle: you scan titles first, then pull only the bodies
    you want — seven full articles at once is unreadable.
    """
    if not doc_ids:
        return []
    placeholders = ",".join("?" * len(doc_ids))
    by_id = {
        r[0]: {"doc_id": r[0], "title": r[1], "domain": r[2], "tier": r[3],
               "published_at": r[4], "body": r[5]}
        for r in conn.execute(
            f"SELECT id, title, domain, tier, published_at, body_full "
            f"FROM articles WHERE id IN ({placeholders})",
            doc_ids,
        )
    }
    return [by_id[i] for i in doc_ids if i in by_id]


def used_source_ids(path: str | Path = DEFAULT_PATH) -> set[int]:
    """Document ids a question has already been written from.

    Passed to `sample_docs(exclude=...)` so repeated runs keep handing you new material
    instead of the same documents. Two questions from one document is not fatal, but it
    wastes annotation effort and correlates two items that should be independent.

    Expected:
        no golden set yet          -> set()
        3 items, one with no source -> {1875, 4471}
    """
    # `load()` already handles the missing-file case and raises on a malformed line —
    # reuse it rather than re-parsing the JSONL here.
    from newsrag.eval.golden import load

    return {it.source_doc_id for it in load(path) if it.source_doc_id is not None}


def sample_docs(
    conn,
    n: int = 20,
    seed: int = 0,
    min_chars: int = MIN_BODY_CHARS,
    exclude: set[int] | None = None,
) -> list[dict]:
    """Pick `n` documents to write questions from, spread across months and domains.

    Args:
        conn:      open corpus DB connection.
        n:         how many documents to return.
        seed:      RNG seed; the same seed must always give the same documents.
        min_chars: minimum body length.
        exclude:   document ids to skip (see `used_source_ids`).
    Returns:
        Up to `n` dicts, ready to read:
        {"doc_id": int, "title": str, "domain": str, "published_at": str, "body": str}

    Expected:
        sample_docs(conn, n=4) -> 4 documents from 4 different (month, domain) buckets,
        and calling it again with the same seed returns the same 4.
    """
    exclude = exclude or set()

    # --- 1. Eligible documents, metadata only ------------------------------- (given)
    # 25k rows of (id, month, domain) is cheap; we fetch full bodies only for the winners.
    rows = conn.execute(
        f"SELECT id, substr(published_at, 1, 7), domain "
        f"FROM articles "
        f"WHERE {_USABLE} AND LENGTH(body_full) >= ? "
        f"ORDER BY id",  # fixed input order, or the seeded shuffle is not reproducible
        (min_chars,),
    ).fetchall()

    # --- 2. Group into (month, domain) buckets ------------------------------ (yours)
    # `buckets` maps ("2024-12", "theguardian.com") -> [id, id, ...].
    # defaultdict(list) lets you append without checking whether the key exists yet.
    # Skip any id present in `exclude` while you build this.
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for doc_id, month, domain in rows:
        if doc_id in exclude:
            continue
        buckets[(month, domain)].append(doc_id)

    # --- 3. Shuffle, seeded ------------------------------------------------- (yours)
    # Use ONE `random.Random(seed)` instance for everything below — the module-level
    # `random.shuffle` reads global state, so results would depend on whatever else ran
    # first, and the promise in the docstring would be false.
    rng = random.Random(seed)
    #   - shuffle the id list inside each bucket, so it isn't always the lowest ids
    #   - build a list of the bucket keys and shuffle that too, so it isn't always
    #     January-Guardian first
    for doc_ids in buckets.values():
        rng.shuffle(doc_ids)
    order = list(buckets)
    rng.shuffle(order)

    # --- 4. Round-robin across buckets -------------------------------------- (yours)
    # Walk the shuffled bucket order taking ONE id from each, then go round again, until
    # you have `n` or every bucket is empty. Taking `n` from the first bucket would defeat
    # the entire point of the stratification.
    # Expected for n=4: 4 ids from 4 different buckets.
    chosen: list[int] = []

    while n > len(chosen):
        before = len(chosen)
        for key in order:
            if len(chosen) >= n:
                break
            if buckets[key]:
                chosen.append(buckets[key].pop())
        if len(chosen) == before:
            break

    # --- 5. Fetch the full rows for the chosen ids -------------------------- (given)
    # Returned in `chosen` order, not database order, so the stratification survives.
    placeholders = ",".join("?" * len(chosen))
    by_id = {
        r[0]: {
            "doc_id": r[0],
            "title": r[1],
            "domain": r[2],
            "published_at": r[3],
            "body": r[4],
        }
        for r in conn.execute(
            f"SELECT id, title, domain, published_at, body_full "
            f"FROM articles WHERE id IN ({placeholders})",
            chosen,
        )
    }
    return [by_id[i] for i in chosen]


def sample_bundles(
    conn,
    n: int = 5,
    seed: int = 0,
    block_days: int = 4,
    min_domains: int = 3,
    exclude: set[int] | None = None,
) -> list[dict]:
    """Groups of documents covering the same club in the same few days, across outlets.

    The input for `multi_doc` and `contradictory` questions — for the drafting model and
    for the human reviewer alike. Titles only; pull bodies with `get_docs`.

    Time is cut into fixed, non-overlapping blocks of `block_days`. Overlapping windows
    would hand back near-identical bundles; the price of fixed blocks is that a story
    straddling a block edge is split in two — measured on the Ten Hag sacking
    (2024-10-28): with 3- or 4-day blocks the edge falls right after the peak day. Accepted,
    because `related_docs` recovers the rest of a story from any article in it. Default 4:
    the user's domain read is that football stories run 3-4 days, and that story's core
    burst (sacking to farewell) ran 28/10-01/11.

    Args:
        conn:        open corpus DB connection.
        n:           how many bundles to return.
        seed:        RNG seed; same seed, same bundles.
        block_days:  width of each time block.
        min_domains: minimum number of distinct outlets in a bundle.
        exclude:     doc ids never to include (e.g. already used as a source).
    Returns:
        Up to `n` bundles, spread across clubs:
        [{"club": "Arsenal", "start": "2024-10-24", "end": "2024-10-26",
          "domains": 4,
          "docs": [{"doc_id": 1194, "title": "...", "domain": "theguardian.com",
                    "tier": "reliable", "published_at": "2024-10-25T..."}, ...]}, ...]

    Expected:
        sample_bundles(conn, n=3) -> 3 bundles, 3 different clubs, each with >= 3 domains,
        and the same 3 again for the same seed.
    """
    exclude = exclude or set()

    # --- 1. Usable documents, metadata only ---------------------------------------
    # ORDER BY id: SQL guarantees no row order without it, and the seeded shuffle below is
    # only reproducible if its input order is fixed.
    rows = conn.execute(
        f"SELECT id, substr(published_at, 1, 10), domain, tier, title, published_at "
        f"FROM articles WHERE {_USABLE} ORDER BY id"
    ).fetchall()

    # --- 2. Group into (club, block) ---------------------------------------------
    # toordinal() is a day count, so integer division puts every `block_days`
    # consecutive days in the same block. A headline naming two clubs ("Arsenal vs
    # Liverpool") joins both bundles: it is evidence for both stories.
    bundles: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for doc_id, day, domain, tier, title, published_at in rows:
        if doc_id in exclude:
            continue
        block = date.fromisoformat(day).toordinal() // block_days
        doc = {"doc_id": doc_id, "title": title, "domain": domain, "tier": tier,
               "published_at": published_at}
        for club in _clubs_in(title):
            bundles[(club, block)].append(doc)

    # --- 3. Keep only multi-outlet bundles ---------------------------------------
    # Seven Guardian pieces on Arsenal are one outlet's view, not several.
    qualified = [
        key for key, docs in bundles.items()
        if len({d["domain"] for d in docs}) >= min_domains
    ]

    # --- 4. Stratify across clubs, seeded ----------------------------------------
    # Same round-robin as sample_docs, with the club as the bucket — otherwise the
    # most-covered clubs (Arsenal, Liverpool, Man City) would fill the whole sample.
    # Keys and clubs are sorted before shuffling so the shuffle's input is fixed.
    rng = random.Random(seed)
    by_club: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for key in sorted(qualified):
        by_club[key[0]].append(key)
    for keys in by_club.values():
        rng.shuffle(keys)
    clubs = sorted(by_club)
    rng.shuffle(clubs)

    chosen: list[tuple[str, int]] = []
    while len(chosen) < n:
        before = len(chosen)
        for club in clubs:
            if len(chosen) >= n:
                break
            if by_club[club]:
                chosen.append(by_club[club].pop())
        if len(chosen) == before:
            break

    # --- 5. Shape the output ------------------------------------------------------
    out = []
    for key in chosen:
        docs = sorted(bundles[key], key=lambda d: d["published_at"])
        out.append({
            "club": key[0],
            "start": docs[0]["published_at"][:10],
            "end": docs[-1]["published_at"][:10],
            "domains": len({d["domain"] for d in docs}),
            "docs": docs,
        })
    return out


def related_docs(conn, doc_id: int, days: int = 4, limit: int = 15) -> list[dict]:
    """Documents naming the same club(s) as `doc_id`, published within ±`days` of it.

    The agent's way to follow a thread: start from an article that looks promising and
    look around it — for the second half of a `multi_doc` answer, or for the outlet that
    contradicts the first. Bounded on purpose: it relates by club and date, never by free
    text. A free-text search tool would act as a retriever, and whatever it surfaced would
    become `relevant_docs` — biasing the golden set toward that retriever (AGENTS §7).

    Args:
        conn:    open corpus DB connection.
        doc_id:  the seed article.
        days:    half-width of the time window around the seed's publication day.
        limit:   maximum documents returned. Needed: a big club has a lot of coverage.
    Returns:
        Closest in time first; ties broken by id so the result is deterministic.
        [{"doc_id": 1204, "title": "...", "domain": "theguardian.com", "tier": "reliable",
          "published_at": "2024-10-25T...", "days_apart": 0}, ...]
        An empty list when the seed's headline names no club — nothing to relate by.

    Raises:
        ValueError: the seed is not in the usable corpus. Raised, not an empty list: for
        an agent, "that id is wrong" and "nothing related" must look different.

    Expected (measured 2026-10-18):
        related_docs(conn, 1194) — "Arteta warns Arsenal ... before Liverpool clash",
        2024-10-25, clubs {Arsenal, Liverpool}: 119 related docs within ±4 days (93 at
        ±3), across 6 domains. With limit=15 you get the 15 closest in time, starting
        with 1204, 13997, 14314, 21965 (all same day).
        Seeded on the Ten Hag sacking (doc 1240, 2024-10-28): 59 related docs spanning
        2024-10-24 to 2024-11-01 — the whole story that a fixed 4-day block cuts in two.
    """
    # --- 1. The seed --------------------------------------------------------- (given)
    seed = conn.execute(
        f"SELECT title, published_at FROM articles WHERE id = ? AND {_USABLE}",
        (doc_id,),
    ).fetchone()
    if seed is None:
        raise ValueError(f"doc {doc_id} is not in the usable corpus")
    seed_title, seed_published = seed

    clubs = _clubs_in(seed_title)
    if not clubs:
        return []

    # --- 2. Candidates in the time window ------------------------------------ (given)
    # Dates filtered in SQL (cheap, uses the ISO-string ordering); clubs filtered in
    # Python, because the aliases make a SQL LIKE unreliable.
    seed_day = date.fromisoformat(seed_published[:10])
    lo = (seed_day - timedelta(days=days)).isoformat()
    hi = (seed_day + timedelta(days=days)).isoformat()
    rows = conn.execute(
        f"SELECT id, title, domain, tier, published_at FROM articles "
        f"WHERE {_USABLE} AND id != ? AND substr(published_at, 1, 10) BETWEEN ? AND ?",
        (doc_id, lo, hi),
    ).fetchall()

    # --- 3. Keep rows that share at least one club with the seed ------------------
    # `&` is set intersection; an empty intersection is falsy, so it is the condition.
    related: list[dict] = []
    for rid, title, domain, tier, published_at in rows:
        if _clubs_in(title) & clubs:
            related.append({
                "doc_id": rid, "title": title, "domain": domain, "tier": tier,
                "published_at": published_at,
                "days_apart": abs((date.fromisoformat(published_at[:10]) - seed_day).days),
            })

    # --- 4. Closest first, deterministic, capped ---------------------------------
    # The doc_id tie-break keeps same-day documents in a fixed order, so the agent sees
    # the same "top N" on every run.
    related.sort(key=lambda d: (d["days_apart"], d["doc_id"]))
    return related[:limit]
