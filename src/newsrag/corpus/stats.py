"""Corpus health report — read-only, no network.

This is the **gate** between Phase 1 and Phase 2: it answers "is this corpus good enough
to build a golden set on?" A golden set annotated over a broken corpus is wasted work,
and it is the single most expensive artifact in the project, so we check first.

The report is a funnel. Articles are lost at every stage — blocked by the source, blocked
by robots, a 403, an extraction failure, a validity rejection, a duplicate — and the
funnel shows *where*. A single "N articles" number hides all of that.

It also validates the two knobs we tuned on tiny samples: the source allowlist (via the
domain distribution) and the dedup threshold (via cluster sizes).
"""

from __future__ import annotations

import statistics
from pathlib import Path

from newsrag.corpus import db


#: Readiness checks that FAIL but were consciously accepted, mapped to the reason.
#:
#: This never changes a threshold and never hides a failure — the check still runs, still
#: reports FAIL, and the real number is still printed above. It only records that a human
#: looked at this specific failure and decided to proceed, so the final verdict doesn't
#: keep saying "not ready" about a closed decision.
#:
#: Deliberately hardcoded rather than config-driven: accepting a gate should require a
#: reviewable code change, not a knob that is easy to turn when a number is inconvenient.
#: Add an entry ONLY after the user explicitly decides, and say who/when/why.
#: Each entry carries `valid_while`: the acceptance applies ONLY while reality still looks
#: like what was actually accepted. A decision about "Guardian at 40.2%" must not silently
#: cover "some other domain at 80%" — otherwise the exception quietly becomes a blanket
#: waiver. When `valid_while` no longer holds, the gate blocks again and says why.
ACCEPTED_FAILURES: dict[str, dict] = {
    "no domain over 40% of usable corpus": {
        "reason": (
            "2026-07-22 by Enrique — chose to proceed with Guardian dominant rather than "
            "pad the corpus, over-collect, or move the threshold. Was 40.2% then; now "
            "41.7% after the 2026-09-13 non-journalism cleanup removed ~1,000 template "
            "pages that had been padding the denominator (a truer number, not a worse "
            "corpus). NOTE: only ~0.3pp of headroom left under the 42% ceiling."
        ),
        "valid_while": lambda ctx: (
            ctx.get("top_domain") == "theguardian.com" and ctx.get("largest_share", 100) <= 42.0
        ),
        "expires_note": "accepted for Guardian at ~40%; a different domain or >42% is a new decision",
    },
}


def _scalar(conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return row[0] if row and row[0] is not None else 0


def _bar(count: int, total: int, width: int = 28) -> str:
    """A tiny inline histogram bar — makes skew visible at a glance."""
    if total <= 0:
        return ""
    filled = round(width * count / total)
    return "█" * filled + "·" * (width - filled)


def stats(db_path=db.DEFAULT_DB_PATH, cache_path=db.DEFAULT_CACHE_PATH) -> dict:
    """Print the corpus health report. Returns the headline numbers as a dict."""
    if not Path(db_path).exists():
        print(f"no corpus DB at {db_path} — run `corpus init` first")
        return {}

    conn = db.connect(db_path)
    cache_conn = db.connect(cache_path) if Path(cache_path).exists() else None
    try:
        total = _scalar(conn, "SELECT COUNT(*) FROM articles")
        if total == 0:
            print("corpus is empty — run `corpus ingest` first")
            return {}

        with_body = _scalar(conn, "SELECT COUNT(*) FROM articles WHERE body_full IS NOT NULL")
        valid = _scalar(conn, "SELECT COUNT(*) FROM articles WHERE body_valid = 1")
        dupes = _scalar(conn, "SELECT COUNT(*) FROM articles WHERE dup_of IS NOT NULL")
        usable = _scalar(
            conn,
            "SELECT COUNT(*) FROM articles WHERE body_valid = 1 AND dup_of IS NULL",
        )

        print("=" * 62)
        print("CORPUS HEALTH")
        print("=" * 62)

        # --- Funnel -------------------------------------------------------------
        print("\nPIPELINE FUNNEL")
        stages = [
            ("ingested (metadata)", total),
            ("HTML cached", _scalar(cache_conn, "SELECT COUNT(*) FROM raw_html") if cache_conn else 0),
            ("body extracted", with_body),
            ("body valid", valid),
            ("canonical (non-duplicate)", usable),
        ]
        for label, count in stages:
            pct = 100 * count / total if total else 0
            print(f"  {label:<28} {count:>7}  {pct:>5.1f}%  {_bar(count, total)}")
        print(f"\n  → USABLE CORPUS: {usable} articles")

        # --- Where articles were lost -------------------------------------------
        print("\nLOSSES")
        pending = total - with_body
        if pending:
            print(f"  not yet extracted            {pending:>7}   (run fetch + extract)")
        for reason, count in conn.execute(
            "SELECT COALESCE(invalid_reason,'unknown'), COUNT(*) FROM articles "
            "WHERE body_valid = 0 GROUP BY 1 ORDER BY 2 DESC"
        ):
            print(f"  invalid: {reason:<20} {count:>7}")
        if dupes:
            print(f"  near-duplicate               {dupes:>7}")

        if cache_conn:
            rows = list(
                cache_conn.execute(
                    "SELECT status_code, COUNT(*) FROM raw_html GROUP BY 1 ORDER BY 2 DESC"
                )
            )
            if rows:
                print("\nFETCH OUTCOMES (cached HTML by HTTP status)")
                cache_total = sum(c for _, c in rows)
                for status, count in rows:
                    note = "" if status == 200 else "  ← not article content"
                    print(f"  {status:<6} {count:>7}  {100*count/cache_total:>5.1f}%{note}")

        # --- Duplicate clusters --------------------------------------------------
        cluster_sizes = [
            n + 1  # +1 for the canonical itself
            for (n,) in conn.execute(
                "SELECT COUNT(*) FROM articles WHERE dup_of IS NOT NULL GROUP BY dup_of"
            )
        ]
        print("\nDUPLICATE CLUSTERS")
        if cluster_sizes:
            print(f"  clusters                     {len(cluster_sizes):>7}")
            print(f"  articles collapsed           {dupes:>7}")
            print(f"  largest cluster              {max(cluster_sizes):>7}")
            if max(cluster_sizes) >= 20:
                print("  ⚠ large cluster — transitive chaining? try a higher --threshold")
        else:
            print("  none detected")

        # --- Domain distribution (validates the allowlist) -----------------------
        # Concentration is measured over the USABLE corpus, not total ingested: a domain
        # with many bodyless/invalid/duplicate rows (e.g. ESPN's empty 202s) would
        # otherwise pad the denominator and hide the real over-representation the golden
        # set would inherit. `share` = domain's usable / total usable.
        print("\nTOP DOMAINS (validates the source allowlist) — % of USABLE corpus")
        domain_rows = list(
            conn.execute(
                "SELECT domain, COUNT(*) AS n, "
                "SUM(CASE WHEN body_valid = 1 AND dup_of IS NULL THEN 1 ELSE 0 END) AS good "
                "FROM articles GROUP BY domain ORDER BY good DESC, n DESC LIMIT 15"
            )
        )
        print(f"  {'domain':<26}{'total':>7}{'usable':>8}{'%usable':>9}")
        for domain, n, good in domain_rows:
            share = 100 * (good or 0) / usable if usable else 0
            flag = "  ⚠ over-represented" if share > 40 else ""
            print(f"  {(domain or '?'):<26}{n:>7}{(good or 0):>8}   {share:>5.1f}%{flag}")

        # --- Temporal coverage ---------------------------------------------------
        months = list(
            conn.execute(
                "SELECT substr(published_at,1,7) AS m, COUNT(*) FROM articles "
                "WHERE published_at IS NOT NULL GROUP BY m ORDER BY m"
            )
        )
        if months:
            print("\nTEMPORAL COVERAGE (gaps break time-filtered retrieval)")
            peak = max(c for _, c in months)
            for month, count in months:
                print(f"  {month}  {count:>6}  {_bar(count, peak, 24)}")

        # --- Body lengths --------------------------------------------------------
        lengths = [
            r[0]
            for r in conn.execute(
                "SELECT length(body_full) FROM articles "
                "WHERE body_valid = 1 AND dup_of IS NULL"
            )
        ]
        median_len = int(statistics.median(lengths)) if lengths else 0
        if lengths:
            print("\nBODY LENGTH (usable articles, characters)")
            print(f"  min / median / max   {min(lengths)} / {median_len} / {max(lengths)}")
            if len(lengths) > 1:
                print(f"  mean                 {int(statistics.mean(lengths))}")

        # --- Readiness gate ------------------------------------------------------
        # Largest usable share across ALL domains (not just the top-15 shown, and by
        # usable count — the denominator that matters for the golden set).
        top_row = conn.execute(
            "SELECT domain, SUM(CASE WHEN body_valid=1 AND dup_of IS NULL THEN 1 ELSE 0 END) g "
            "FROM articles GROUP BY domain ORDER BY g DESC LIMIT 1"
        ).fetchone()
        top_domain, top_usable = (top_row[0], top_row[1] or 0) if top_row else (None, 0)
        largest_share = (100 * top_usable / usable) if usable else 0
        checks = [
            ("enough usable articles (≥1000)", usable >= 1000),
            ("median body ≥ 800 chars", median_len >= 800),
            ("no domain over 40% of usable corpus", largest_share <= 40),
            ("largest dup cluster < 20", max(cluster_sizes, default=0) < 20),
        ]
        ctx = {"top_domain": top_domain, "largest_share": largest_share, "usable": usable}
        print("\nREADY FOR THE GOLDEN SET (Phase 2)?")
        unaddressed = []
        accepted_any = False
        for label, ok in checks:
            if ok:
                print(f"  PASS  {label}")
                continue
            print(f"  FAIL  {label}")
            exc = ACCEPTED_FAILURES.get(label)
            if exc and exc["valid_while"](ctx):
                print(f"        └─ accepted {exc['reason']}")
                accepted_any = True
            elif exc:
                # The decision existed but reality drifted away from what was accepted.
                print(f"        └─ ⚠ prior acceptance NO LONGER APPLIES "
                      f"({exc['expires_note']}) — this needs a new decision")
                unaddressed.append(label)
            else:
                unaddressed.append(label)

        if unaddressed:
            print("\n  ⛔ not ready — unaddressed failures above")
        elif accepted_any:
            print("\n  ✅ ready — proceeding with documented accepted exception(s)")
            print("     (the FAIL above is real and stays reported; it was a decision)")
        else:
            print("\n  ✅ corpus looks ready — proceed to Phase 2")
        print()
    finally:
        conn.close()
        if cache_conn:
            cache_conn.close()

    return {
        "total": total,
        "with_body": with_body,
        "valid": valid,
        "duplicates": dupes,
        "usable": usable,
        "median_body_chars": median_len,
    }
