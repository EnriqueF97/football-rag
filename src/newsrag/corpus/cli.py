"""Command-line entry point for corpus construction.

Exposed as the `corpus` console script (see pyproject.toml [project.scripts]).
Run `corpus --help` to list subcommands.

Every subcommand is a stub in Phase 0 — it parses arguments and then raises
NotImplementedError, so the interface is real and testable before the logic lands.
Each is filled in during Phase 1 (see project_progress.md).
"""

from __future__ import annotations

import argparse
import sys


def _not_implemented(phase: str):
    """Return a subcommand handler that fails loudly, pointing at its plan phase."""

    def handler(args: argparse.Namespace) -> int:
        raise NotImplementedError(
            f"`corpus {args.command}` is not implemented yet — see {phase} "
            "in project_progress.md"
        )

    return handler


def _init(args: argparse.Namespace) -> int:
    """Create both SQLite databases (corpus + HTML cache). Idempotent."""
    from newsrag.corpus import db

    corpus_path = db.init_corpus()
    cache_path = db.init_cache()
    print(f"corpus DB ready:     {corpus_path}  (articles, chunks)")
    print(f"HTML cache DB ready: {cache_path}  (raw_html)")
    return 0


def _ingest(args: argparse.Namespace) -> int:
    """Fetch GDELT headlines into the articles table (metadata only)."""
    from newsrag.corpus import gdelt

    gdelt.ingest(config_path=args.config)
    return 0


def _ingest_guardian(args: argparse.Namespace) -> int:
    """Fetch Guardian articles (metadata + bodies) via the Open Platform API."""
    from newsrag.corpus import guardian

    guardian.ingest(config_path=args.config)
    return 0


def _ingest_mediacloud(args: argparse.Namespace) -> int:
    """Discover reliable-tier articles (BBC/Sky/ESPN) via the Media Cloud API."""
    from newsrag.corpus import mediacloud_ingest

    mediacloud_ingest.ingest(config_path=args.config)
    return 0


def _ingest_cc(args: argparse.Namespace) -> int:
    """Discover tabloid-tier articles in the Common Crawl index (metadata + pointers)."""
    from newsrag.corpus import cc

    cc.discover(config_path=args.config, max_crawls=args.max_crawls)
    return 0


def _fetch_cc(args: argparse.Namespace) -> int:
    """Download discovered bodies as WARC byte-ranges into the HTML cache."""
    from newsrag.corpus import cc

    cc.fetch_bodies(concurrency=args.concurrency, limit=args.limit)
    return 0


def _fetch(args: argparse.Namespace) -> int:
    """Download article HTML into the cache (async, robots-aware)."""
    from newsrag.corpus import fetch

    fetch.fetch(
        per_domain=args.per_domain,
        max_concurrency=args.max_concurrency,
        respect_robots=not args.no_robots,
        limit=args.limit,
    )
    return 0


def _extract(args: argparse.Namespace) -> int:
    """Extract full clean bodies from the cached HTML (or just re-apply validity rules)."""
    from newsrag.corpus import extract

    if args.revalidate:
        extract.revalidate(config_path=args.config)
    else:
        extract.extract(config_path=args.config, reextract=args.reextract)
    return 0


def _dedup(args: argparse.Namespace) -> int:
    """Mark near-duplicate articles, pointing dup_of at the canonical one."""
    from newsrag.corpus import dedup

    dedup.dedup(threshold=args.threshold)
    return 0


def _audit(args: argparse.Namespace) -> int:
    """LLM-assisted audit (L4): report non-journalism candidates for human review."""
    from newsrag.corpus import audit

    audit.audit(
        model=args.model, limit=args.limit, scope=args.scope,
        reason=args.reason, title_like=args.title_like, out=args.out,
        chars=args.chars,
    )
    return 0


def _annotate(args: argparse.Namespace) -> int:
    """Blind human annotation, used to validate the LLM classifier."""
    from newsrag.corpus import annotate

    annotate.annotate(input_path=args.input, out=args.out, limit=args.limit,
                      chars=args.chars)
    return 0


def _stats(args: argparse.Namespace) -> int:
    """Print the corpus health report (read-only)."""
    from newsrag.corpus import stats

    stats.stats()
    return 0


def _smell(args: argparse.Namespace) -> int:
    """Run the per-document smell-test battery (read-only)."""
    from newsrag.corpus import smell

    smell.smell()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpus",
        description="Build the football-news corpus: ingest, fetch, extract, dedup.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create the SQLite databases (schema only)")
    p_init.set_defaults(func=_init)

    p_ingest = sub.add_parser("ingest", help="fetch GDELT headlines into the DB")
    p_ingest.add_argument(
        "--config", default="config/football_queries.yaml",
        help="query config YAML (default: config/football_queries.yaml)",
    )
    p_ingest.set_defaults(func=_ingest)

    p_guardian = sub.add_parser(
        "ingest-guardian", help="fetch Guardian articles (metadata + bodies) via its API"
    )
    p_guardian.add_argument(
        "--config", default="config/football_queries.yaml",
        help="config YAML (uses date_range and the guardian: section)",
    )
    p_guardian.set_defaults(func=_ingest_guardian)

    p_mc = sub.add_parser(
        "ingest-mediacloud",
        help="discover reliable-tier articles (BBC/Sky/ESPN) via Media Cloud",
    )
    p_mc.add_argument(
        "--config", default="config/football_queries.yaml",
        help="config YAML (uses date_range and the mediacloud: section)",
    )
    p_mc.set_defaults(func=_ingest_mediacloud)

    p_cc = sub.add_parser(
        "ingest-cc", help="discover tabloid-tier articles in the Common Crawl index"
    )
    p_cc.add_argument(
        "--config", default="config/football_queries.yaml",
        help="config YAML (uses date_range and the commoncrawl: section)",
    )
    p_cc.add_argument(
        "--max-crawls", type=int, default=None,
        help="only the first N crawls (useful for testing)",
    )
    p_cc.set_defaults(func=_ingest_cc)

    p_fcc = sub.add_parser(
        "fetch-cc", help="download discovered bodies as WARC byte-ranges into the cache"
    )
    p_fcc.add_argument(
        "--concurrency", type=int, default=3,
        help="simultaneous range requests to data.commoncrawl.org (default: 3 — "
             "higher trips CloudFront's IP block)",
    )
    p_fcc.add_argument(
        "--limit", type=int, default=None,
        help="fetch at most N bodies (useful for testing)",
    )
    p_fcc.set_defaults(func=_fetch_cc)

    p_fetch = sub.add_parser("fetch", help="download article HTML into the cache")
    p_fetch.add_argument(
        "--per-domain", type=int, default=3,
        help="max simultaneous requests PER DOMAIN (default: 3)",
    )
    p_fetch.add_argument(
        "--max-concurrency", type=int, default=15,
        help="global max simultaneous requests (default: 15)",
    )
    p_fetch.add_argument(
        "--no-robots", action="store_true",
        help="do NOT honour robots.txt (conscious override)",
    )
    p_fetch.add_argument(
        "--limit", type=int, default=None,
        help="fetch at most N articles (useful for testing)",
    )
    p_fetch.set_defaults(func=_fetch)

    p_extract = sub.add_parser("extract", help="extract full bodies from cached HTML")
    p_extract.add_argument(
        "--reextract", action="store_true",
        help="re-run extraction on already-extracted articles",
    )
    p_extract.add_argument(
        "--revalidate", action="store_true",
        help="re-apply validity rules to bodies already in the DB (no re-extraction, "
             "seconds not minutes) — use after tuning the `validity:` rules",
    )
    p_extract.add_argument(
        "--config", default="config/football_queries.yaml",
        help="config YAML holding the `validity:` rules",
    )
    p_extract.set_defaults(func=_extract)

    p_dedup = sub.add_parser("dedup", help="detect and mark near-duplicate articles")
    p_dedup.add_argument(
        "--threshold", type=float, default=0.75,
        help="MinHash Jaccard similarity cutoff, 0..1 (default: 0.75)",
    )
    p_dedup.set_defaults(func=_dedup)

    p_audit = sub.add_parser(
        "audit",
        help="LLM-assisted audit (L4): flag non-journalism candidates for human review",
    )
    p_audit.add_argument("--model", default="gemma4:e2b",
                         help="Ollama model (default: gemma4:e2b — measured best here)")
    p_audit.add_argument("--limit", type=int, default=300, help="sample size (default: 300)")
    p_audit.add_argument("--scope", choices=["usable", "invalid", "all"], default="usable",
                         help="audit what the filters kept ('usable') or rejected ('invalid')")
    p_audit.add_argument("--reason", default=None,
                         help="with --scope invalid: only this invalid_reason")
    p_audit.add_argument("--title-like", default=None,
                         help="extra SQL LIKE filter on the title, e.g. '%%preview%%'")
    p_audit.add_argument("--out", default=None, help="write full results to this JSON file")
    p_audit.add_argument("--chars", type=int, default=400,
                         help="characters of body the model reads (default: 400)")
    p_audit.set_defaults(func=_audit)

    p_annot = sub.add_parser(
        "annotate", help="blind human labelling, to validate the LLM classifier")
    p_annot.add_argument("--input", default=None,
                         help="JSON file from `corpus classify` (default: sample the corpus)")
    p_annot.add_argument("--out", default="data/golden/label_validation.jsonl",
                         help="where labels are appended (resumable)")
    p_annot.add_argument("--limit", type=int, default=100, help="how many to show")
    p_annot.add_argument("--chars", type=int, default=1600,
                         help="characters of body to display (default: 1600)")
    p_annot.set_defaults(func=_annotate)

    p_stats = sub.add_parser("stats", help="print a corpus health summary")
    p_stats.set_defaults(func=_stats)

    p_smell = sub.add_parser(
        "smell", help="per-document anomaly checks over the usable corpus"
    )
    p_smell.set_defaults(func=_smell)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
