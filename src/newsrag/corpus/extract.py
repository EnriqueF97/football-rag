"""Full-body extraction from cached HTML.

This is the correction at the heart of the project. The legacy scraper grabbed the first
three ``<p>`` tags — enough for a sentiment score, useless for RAG, which needs the whole
article to chunk. Here we extract the *full* body with ``trafilatura`` (which strips nav,
footers, and boilerplate), repair encoding with ``ftfy``, and gate the result through a
validity filter so block pages, paywall stubs, and off-topic GDELT noise never enter the
corpus disguised as articles.

Only HTTP **200** responses are extracted — a 403 block page is not an article.

Resume: only rows with ``body_full IS NULL`` are processed, so re-running is cheap.
Re-extraction after improving this module is driven by ``--reextract``, and never
re-downloads anything because the HTML cache is a separate database.
"""

from __future__ import annotations

import gzip
import hashlib
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import ftfy
import trafilatura
import yaml

from newsrag.corpus import db

#: Bump this when extraction logic changes, so we know which rows to re-extract and can
#: compare extractor quality as an ablation.
EXTRACTOR_VERSION = "trafilatura+ftfy/v1"

DEFAULT_CONFIG = Path("config/football_queries.yaml")

_FALLBACK_RULES = {
    "min_chars": 400,
    "min_keyword_hits": 2,
    "keywords": ["football", "soccer", "club", "league", "match", "goal", "player"],
    "spam_phrases": ["enable javascript", "accept all cookies"],
}


def load_rules(config_path: str | Path = DEFAULT_CONFIG) -> dict:
    """Read the `validity:` section from the domain config (falls back to defaults)."""
    try:
        with open(Path(config_path)) as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return dict(_FALLBACK_RULES)
    rules = dict(_FALLBACK_RULES)
    rules.update(cfg.get("validity") or {})
    return rules


# Leading nav breadcrumb some sites (TEAMtalk especially) leave at the top of the body:
# "Chelsea\nPremier League • England\n<real headline>…". The "<competition> • <country>"
# line is the signature. Strip everything up to and including it, but only when it sits
# right at the start (first ~120 chars), so a legitimate mid-article "• England" is safe.
_BREADCRUMB = re.compile(
    r"•\s*(England|Spain|Italy|Germany|France|Scotland|Wales|Netherlands|Portugal|"
    r"Europe|World|Brazil|Argentina|Turkey|Belgium|USA)\s*\n"
)

# BBC bodies come out as "<headline repeated>\n- Published\n<real body>": trafilatura
# keeps the headline line and the "- Published" timestamp label. Drop both — the headline
# is already in `title`, the label is chrome. Anchored to the start with a one-line
# headline (≤250 chars) so it can't eat real content.
_BBC_PUBLISHED = re.compile(r"^[^\n]{0,250}\n-\s*Published\s*\n")
# BBC also appends related-article teasers as whole lines "<headline> - Published<date>"
# and, occasionally, bare "- Published" label lines. Both are chrome — drop the entire
# teaser line, and any leftover bare label line.
_BBC_TEASER = re.compile(r"[^\n]*-\s*Published\s*\d[^\n]*\n?")
_PUBLISHED_LINE = re.compile(r"\n-\s*Published\s*(?=\n|$)")


def strip_boilerplate(body: str) -> str:
    """Remove boilerplate trafilatura leaves in some bodies: leading nav breadcrumbs and
    BBC '- Published' timestamp chrome (leading headline block + embedded label lines)."""
    m = _BREADCRUMB.search(body[:120])
    if m:
        body = body[m.end():]
    body = _BBC_PUBLISHED.sub("", body, count=1)     # leading "headline\n- Published\n"
    body = _BBC_TEASER.sub("", body)                 # related-article teaser lines
    body = _PUBLISHED_LINE.sub("", body)             # any remaining bare label lines
    # Stray leaked labels occasionally left on their own line at the top.
    body = re.sub(r"^(?:-\s*Published|Published|Advertisement)\s*\n", "", body)
    return body.strip()


def _normalize(text: str) -> str:
    """Collapse whitespace and lowercase — the canonical form used for hashing."""
    return re.sub(r"\s+", " ", text).strip().lower()


def content_hash(text: str) -> str:
    """SHA-256 of the normalized body. Anchors exact-duplicate detection."""
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def _non_journalism_patterns(patterns: tuple[str, ...]) -> list[re.Pattern]:
    """Compile the title patterns once (cached — this runs over tens of thousands of rows)."""
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def is_non_journalism(title: str | None, url: str | None, rules: dict) -> bool:
    """Is this a template page rather than journalism?

    TV/streaming listings, section and player hub pages, affiliate/sponsored posts,
    fantasy tips and quizzes answer no question: they waste golden-set annotation time
    and surface as retrieval false positives. All patterns live in the config — see
    `validity.non_journalism_titles`, `_urls` and `_exemptions`.

    Order matters: **exemptions win**. A phrase can appear in both a template and a real
    article ("how to watch" shows up in TV guides *and* in football365's match previews),
    so a positive marker of journalism overrides the junk match.
    """
    exemptions = tuple(rules.get("non_journalism_exemptions") or ())
    if title and any(p.search(title) for p in _non_journalism_patterns(exemptions)):
        return False
    if title:
        title_pats = tuple(rules.get("non_journalism_titles") or ())
        if any(p.search(title) for p in _non_journalism_patterns(title_pats)):
            return True
    if url:
        url_pats = tuple(rules.get("non_journalism_urls") or ())
        if any(p.search(url) for p in _non_journalism_patterns(url_pats)):
            return True
    return False


def is_valid_body(
    text: str | None, rules: dict, title: str | None = None, url: str | None = None
) -> tuple[bool, str]:
    """Is this a usable article? Returns (valid, reason) — the reason feeds `stats`.

    `title`/`url` are optional so existing callers keep working, but pass them when you
    have them: the non-journalism check needs both.
    """
    if not text:
        return False, "empty"
    if is_non_journalism(title, url, rules):
        return False, "non_journalism"
    if len(text) < rules["min_chars"]:
        return False, "too_short"
    low = text.lower()
    for phrase in rules["spam_phrases"]:
        if phrase.lower() in low:
            return False, "spam_phrase"
    hits = sum(1 for kw in rules["keywords"] if kw.lower() in low)
    if hits < rules["min_keyword_hits"]:
        return False, "off_topic"
    return True, "ok"


def revalidate(
    db_path=db.DEFAULT_DB_PATH,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict:
    """Re-apply the validity rules to bodies already in the DB — no re-extraction.

    Tuning a validity rule should be cheap. This re-runs `is_valid_body` over existing
    `body_full` values (never touching trafilatura or the HTML cache), so a rule change
    costs seconds instead of a full re-extract. Reversible: it only rewrites
    `body_valid` / `invalid_reason`, so relaxing a rule and re-running restores rows.
    """
    rules = load_rules(config_path)
    conn = db.connect(db_path)
    tally: Counter = Counter()
    try:
        rows = conn.execute(
            "SELECT id, title, url, body_full, body_valid FROM articles "
            "WHERE body_full IS NOT NULL"
        ).fetchall()
        updates, changed = [], 0
        for article_id, title, url, body, was_valid in rows:
            valid, reason = is_valid_body(body, rules, title, url)
            tally[reason] += 1
            if int(valid) != (was_valid or 0):
                changed += 1
            updates.append((int(valid), None if valid else reason, article_id))
        conn.executemany(
            "UPDATE articles SET body_valid = ?, invalid_reason = ? WHERE id = ?", updates
        )
        conn.commit()
    finally:
        conn.close()

    print(f"revalidated: {len(rows)}  |  changed: {changed}")
    print(f"outcomes: {dict(tally)}")
    return dict(tally)


def extract(
    db_path=db.DEFAULT_DB_PATH,
    cache_path=db.DEFAULT_CACHE_PATH,
    config_path: str | Path = DEFAULT_CONFIG,
    reextract: bool = False,
) -> dict:
    """Extract bodies for articles whose HTML is cached. Returns an outcome tally."""
    rules = load_rules(config_path)
    conn = db.connect(db_path)
    cache_conn = db.connect(cache_path)
    tally: Counter = Counter()
    try:
        sql = "SELECT id, url, title FROM articles"
        if not reextract:
            sql += " WHERE body_full IS NULL"
        rows = conn.execute(sql).fetchall()

        # Only 200s are real content; a cached 403 is a block page.
        cached = dict(
            cache_conn.execute("SELECT url, html_gz FROM raw_html WHERE status_code = 200")
        )

        updates = []
        for article_id, url, title in rows:
            blob = cached.get(url)
            if blob is None:
                tally["no_cached_200"] += 1
                continue
            body = trafilatura.extract(
                gzip.decompress(blob), include_comments=False, include_tables=False
            )
            if not body:
                tally["extract_failed"] += 1
                continue
            body = strip_boilerplate(ftfy.fix_text(body))
            valid, reason = is_valid_body(body, rules, title, url)
            tally[reason] += 1
            updates.append(
                (
                    body,
                    int(valid),
                    None if valid else reason,  # persisted so `stats` can explain losses
                    content_hash(body),
                    EXTRACTOR_VERSION,
                    article_id,
                )
            )

        conn.executemany(
            "UPDATE articles SET body_full = ?, body_valid = ?, invalid_reason = ?, "
            "content_hash = ?, extractor_version = ? WHERE id = ?",
            updates,
        )
        conn.commit()
    finally:
        conn.close()
        cache_conn.close()

    print(f"candidates: {len(rows)}  |  extracted: {len(updates)}")
    print(f"outcomes: {dict(tally)}")
    return dict(tally)
