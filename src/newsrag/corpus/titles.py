"""Repair article titles whose HTML entities were mangled upstream.

THE DEFECT (found 2026-10-21, while building the golden-set audit screen)
Sky Sports writes apostrophes, double quotes and ampersands as HTML entities — `&#x27;`,
`&quot;`, `&amp;`. Media Cloud, which supplied the titles at discovery, decoded them badly:
each entity became a space plus a stray `;`, and the whitespace was then collapsed.

    Sky's HTML                         title stored in the corpus
    Sjoeke Nusken&#x27;s strike   ->   Sjoeke Nusken ;s strike
    Martin O&#x27;Neill           ->   Martin O ;Neill
    Hearts, Celtic &amp; Rangers  ->   Hearts, Celtic ; Rangers
    relegation &quot;fear&quot;   ->   relegation ;fear ;

Measured: 327 titles, 312 of them usable, all Sky Sports but one TeamTalk. Bodies are
clean — they are extracted from the HTML by trafilatura, not taken from Media Cloud.

WHY NOT A TEXT REPLACE
" ;" was sometimes an apostrophe, sometimes `&`, sometimes a quote mark, and the space
before it was sometimes real ("and 'seven finals'") and sometimes not ("Nusken's"). The
broken title does not say which. So the original is recovered from the source instead:
the page's own HTML, still in the fetch cache.

WHY IT IS SAFE: EVERY REPAIR IS VERIFIED
A title from the HTML is used only if breaking it again with the same rule (`mangle`)
reproduces the stored title exactly — or, where Sky appended a suffix (" - Paper Talk",
" - live on Sky Sports"), a word-boundary prefix of it does. Anything that does not
round-trip — mostly headlines Sky rewrote after discovery — is left untouched by default.

This module only changes `title`. It never touches bodies, and an LLM never writes a
title: the repair is deterministic (AGENTS.md §6).
"""

from __future__ import annotations

import gzip
import html
import re
from pathlib import Path

from newsrag.corpus import db

#: Characters Sky encodes as entities, every one of which Media Cloud turned into " ;".
_ENTITY_CHARS = "'&\""

#: Titles to inspect — the artifact always leaves a space before a semicolon.
_AFFECTED = "title LIKE '% ;%'"

_OG_TITLE = re.compile(r'property="og:title"\s+content="([^"]*)"', re.I)
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def mangle(title: str) -> str:
    """Reproduce Media Cloud's defect: each entity-encoded character -> " ;", spaces collapsed.

    Used only to VERIFY a candidate repair, never to produce one.
    """
    for ch in _ENTITY_CHARS:
        title = title.replace(ch, " ;")
    return " ".join(title.split())


def html_titles(page: str) -> list[str]:
    """The page's own titles, decoded: og:title first (cleanest), then <title>."""
    found = _OG_TITLE.findall(page) + _TITLE_TAG.findall(page)
    return [html.unescape(t).strip() for t in found if t.strip()]


def restore(stored: str, original: str) -> str | None:
    """The original title whose mangled form is exactly `stored`; None if there is none.

    Tries the whole original first, then shorter word-boundary prefixes of it — Sky
    sometimes appends " - <section>" to the HTML title that Media Cloud did not keep.

    Expected:
        restore("Martin O ;Neill on Celtic", "Martin O'Neill on Celtic")     -> "Martin O'Neill on Celtic"
        restore("Slot ;s Reds weigh swoop", "Slot's Reds weigh swoop - Paper Talk")
                                                                            -> "Slot's Reds weigh swoop"
        restore("Old headline ;s text", "A rewritten headline")             -> None
    """
    target = " ".join(stored.split())
    words = original.split()
    for k in range(len(words), 0, -1):
        candidate = " ".join(words[:k])
        if mangle(candidate) == target:
            return candidate
    return None


def plan(corpus_conn, cache_conn, use_current_html_title: bool = False) -> dict:
    """Work out every change without making any. Returns a report dict.

    {"restored":      [(id, old, new), ...],   verified round-trip repairs
     "current_title": [(id, old, new), ...],   only if use_current_html_title: usable rows
                                               whose headline Sky rewrote — the HTML's
                                               title replaces Media Cloud's old one
     "untouched":     [(id, old, reason), ...]}

    WHY "current_title" IS OPT-IN: it changes words, not just punctuation. Its case for
    being right is that the stored body was extracted from that same page, so the page's
    title describes that body; its risk is replacing a headline wholesale. A human picks.
    Never applied to unusable rows (they are never indexed) or to "Page Not Found" pages.
    """
    rows = corpus_conn.execute(
        f"SELECT id, url, title, COALESCE(body_valid = 1 AND dup_of IS NULL, 0) "
        f"FROM articles WHERE {_AFFECTED} ORDER BY id"
    ).fetchall()
    report = {"restored": [], "current_title": [], "untouched": []}
    for doc_id, url, title, usable in rows:
        hit = cache_conn.execute("SELECT html_gz FROM raw_html WHERE url = ?", (url,)).fetchone()
        if not hit or not hit[0]:
            report["untouched"].append((doc_id, title, "no cached HTML"))
            continue
        candidates = html_titles(gzip.decompress(hit[0]).decode("utf-8", "replace"))
        new = next((r for r in (restore(title, c) for c in candidates) if r), None)
        if new:
            report["restored"].append((doc_id, title, new))
            continue
        current = candidates[0] if candidates else ""
        if use_current_html_title and usable and current and "not found" not in current.lower():
            report["current_title"].append((doc_id, title, current))
        else:
            reason = "headline rewritten by Sky" if current and "not found" not in current.lower() \
                else "page no longer exists"
            report["untouched"].append((doc_id, title, reason + ("" if usable else " (unusable row)")))
    return report


def repair_titles(apply: bool = False, use_current_html_title: bool = False,
                  corpus_path: str | Path = db.DEFAULT_DB_PATH,
                  cache_path: str | Path = db.DEFAULT_CACHE_PATH) -> dict:
    """Plan the repair, print it, and write it only when `apply` is True.

    Dry run by default (WORKING_AGREEMENT §6: show what would change before anything
    destructive). With `apply`, all updates run in ONE transaction: either every planned
    title changes, or — on any error — none does.
    """
    corpus_conn = db.connect(corpus_path)
    cache_conn = db.connect(cache_path)
    report = plan(corpus_conn, cache_conn, use_current_html_title)

    changes = report["restored"] + report["current_title"]
    print(f"titles with the ' ;' artifact: {sum(len(v) for v in report.values())}")
    print(f"  restored from the page's HTML (verified round-trip): {len(report['restored'])}")
    if use_current_html_title:
        print(f"  replaced by the page's current title (opt-in):      {len(report['current_title'])}")
    print(f"  left untouched:                                      {len(report['untouched'])}")
    for label, rows in (("restored", report["restored"][:5]),
                        ("current title", report["current_title"][:5])):
        for doc_id, old, new in rows:
            print(f"\n  [{label}] doc {doc_id}\n    before: {old}\n    after:  {new}")
    for doc_id, old, reason in report["untouched"]:
        print(f"\n  [untouched] doc {doc_id} — {reason}\n    {old}")

    if not apply:
        print(f"\nDRY RUN — nothing written. Re-run with --apply to change {len(changes)} titles.")
        return report

    with corpus_conn:  # one transaction: commits on success, rolls back on any exception
        corpus_conn.executemany("UPDATE articles SET title = ? WHERE id = ?",
                                [(new, doc_id) for doc_id, _, new in changes])
    print(f"\nAPPLIED — {len(changes)} titles updated.")
    return report
