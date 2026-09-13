"""Smell-test battery — per-document quality checks the aggregate gates can't see.

`stats.py` validates the corpus in aggregate (counts, length distribution, domain
balance, dedup clusters). Those pass even when individual documents are junk — a Spanish
page, an author profile, a body with nav breadcrumbs leaking in. This module is the
complement: a battery of high-precision *anomaly* queries that hunt for classes of bad
documents inside the "usable" set (``body_valid = 1 AND dup_of IS NULL``).

It never deletes anything — every check reports a count + a few examples so a human
decides. Think of it as a repeatable version of the ad-hoc queries that first surfaced
the contamination: run it every build, alongside ``stats``, so a new bad class shows up
the day it enters instead of in the golden set.

Severity: ``high`` = almost certainly not a usable article; ``warn`` = review; ``info`` =
distribution FYI, not necessarily wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

from newsrag.corpus import db

# Window the corpus is scoped to (see config date_range). Kept here so the check is
# self-contained; widen if the config window changes.
WINDOW_START = "2024-08-01"
WINDOW_END = "2026-07-01"

# The "usable" definition every check runs against — the thing we are validating.
_USABLE = "body_valid = 1 AND dup_of IS NULL"

# High-precision Spanish markers that almost never appear in English football prose.
# Density (hits per 1k chars) above a threshold flags a foreign-language body.
_ES_MARKERS = re.compile(
    r"\b(según|fútbol|está|también|jugadores|equipo|temporada|además|"
    r"mercado|fichaje|delantero|jugará|técnico)\b",
    re.I,
)


def _sql_check(conn, where: str, limit: int = 3):
    """Count usable rows matching `where`, plus a few (domain, title, url) examples."""
    n = conn.execute(
        f"SELECT COUNT(*) FROM articles WHERE {_USABLE} AND ({where})"
    ).fetchone()[0]
    examples = conn.execute(
        f"SELECT domain, substr(title,1,58), substr(url,1,70) FROM articles "
        f"WHERE {_USABLE} AND ({where}) LIMIT {limit}"
    ).fetchall()
    return n, examples


# (label, severity, WHERE clause) — SQL-expressible checks.
_SQL_CHECKS: list[tuple[str, str, str]] = [
    ("paywall / access stub in body", "high",
     "lower(body_full) LIKE '%subscribe to continue%' "
     "OR lower(body_full) LIKE '%register to continue%' "
     "OR lower(body_full) LIKE '%create a free account%' "
     "OR lower(body_full) LIKE '%sign in to read%'"),
    ("missing or empty title", "high",
     "title IS NULL OR trim(title) = ''"),
    ("residual site suffix in title (| Site)", "high",
     "title LIKE '%|%'"),
    ("nav breadcrumb leaking into body", "warn",
     "body_full LIKE '%• England%' OR body_full LIKE '%• Spain%' "
     "OR body_full LIKE '%• Italy%' OR body_full LIKE '%• Germany%' "
     "OR body_full LIKE '%• France%'"),
    ("UI/cookie boilerplate in body", "warn",
     "lower(body_full) LIKE '%accept all cookies%' "
     "OR lower(body_full) LIKE '%enable javascript%' "
     "OR lower(body_full) LIKE '%skip to content%'"),
    ("stub-length body (400–600 chars)", "warn",
     "length(body_full) BETWEEN 400 AND 600"),
    ("published date outside window or missing", "warn",
     f"published_at IS NULL OR published_at < '{WINDOW_START}' "
     f"OR published_at >= '{WINDOW_END}'"),
    ("very long body (>20k, possible page dump)", "info",
     "length(body_full) > 20000"),
]


def _python_checks(conn):
    """Checks that need per-row logic: foreign language, name-like titles, exact-dup
    content hashes that dedup should have collapsed."""
    results = []

    # 1. Foreign-language bodies (Spanish-marker density).
    rows = conn.execute(
        f"SELECT domain, title, url, body_full FROM articles WHERE {_USABLE}"
    ).fetchall()
    foreign = []
    name_titles = []
    for domain, title, url, body in rows:
        if body:
            hits = len(_ES_MARKERS.findall(body[:2000]))
            if hits >= 5:  # dense Spanish in the opening → not an English article
                foreign.append((domain, (title or "")[:58], (url or "")[:70]))
        # Name-like title: ≤2 words, all capitalised, with a short body → profile page.
        if title:
            words = title.split()
            if (len(words) <= 2 and body and len(body) < 1500
                    and all(w[:1].isupper() for w in words if w)):
                name_titles.append((domain, title[:58], (url or "")[:70]))
    results.append(("foreign-language body (Spanish markers)", "high", foreign))
    results.append(("name-like title + short body (profile?)", "warn", name_titles))

    # 2. Exact-duplicate content among canonicals — dedup should have caught these.
    dup_hashes = conn.execute(
        f"SELECT content_hash FROM articles WHERE {_USABLE} AND content_hash IS NOT NULL "
        "GROUP BY content_hash HAVING COUNT(*) > 1"
    ).fetchall()
    dup_examples = []
    if dup_hashes:
        h = dup_hashes[0][0]
        dup_examples = conn.execute(
            f"SELECT domain, substr(title,1,58), substr(url,1,70) FROM articles "
            f"WHERE {_USABLE} AND content_hash = ? LIMIT 3", (h,)
        ).fetchall()
    results.append(
        (f"exact-dup content across {len(dup_hashes)} hash(es) (dedup miss)",
         "high", dup_examples if dup_hashes else [])
    )
    return results


_SEV_ORDER = {"high": 0, "warn": 1, "info": 2}
_SEV_MARK = {"high": "⛔", "warn": "⚠ ", "info": "· "}


def smell(db_path=db.DEFAULT_DB_PATH) -> dict:
    """Run the battery over the usable corpus. Prints a report, returns {label: count}."""
    conn = db.connect(db_path)
    try:
        usable = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE {_USABLE}"
        ).fetchone()[0]

        findings = []  # (label, severity, count, examples)
        for label, sev, where in _SQL_CHECKS:
            n, ex = _sql_check(conn, where)
            findings.append((label, sev, n, ex))
        for label, sev, ex in _python_checks(conn):
            findings.append((label, sev, len(ex), ex))
    finally:
        conn.close()

    findings.sort(key=lambda f: (_SEV_ORDER[f[1]], -f[2]))

    print("=" * 62)
    print("SMELL TEST — per-document anomalies in the usable corpus")
    print("=" * 62)
    print(f"usable articles under test: {usable}\n")

    flagged_total = 0
    for label, sev, n, ex in findings:
        pct = 100 * n / usable if usable else 0
        if n:
            flagged_total += n
        print(f"{_SEV_MARK[sev]} {n:>5} ({pct:4.1f}%)  {label}")
        for domain, title, url in ex[:3]:
            print(f"          {domain:<18} {title}")
    print()
    high = sum(n for _, s, n, _ in findings if s == "high")
    if high == 0:
        print("✅ no high-severity anomalies — usable set looks clean at this level.")
    else:
        print(f"⛔ {high} high-severity flags — review before trusting the usable set.")
    print("Counts overlap (a doc can trip several checks); this is a review aid, not a\n"
          "delete list. Read the examples and decide.")
    return {label: n for label, _, n, _ in findings}
