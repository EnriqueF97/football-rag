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
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from newsrag.corpus.smell import _USABLE
from newsrag.eval.golden import DEFAULT_PATH

#: Below this, a body is a stub or a teaser — not enough prose to support a real question.
#: 23,128 of the 25,215 usable documents clear this bar, so it costs us almost nothing.
MIN_BODY_CHARS = 1500


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
        f"WHERE {_USABLE} AND LENGTH(body_full) >= ?",
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
