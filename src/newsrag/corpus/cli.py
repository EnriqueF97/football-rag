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
    """Extract full clean bodies from the cached HTML."""
    from newsrag.corpus import extract

    extract.extract(config_path=args.config, reextract=args.reextract)
    return 0


def _dedup(args: argparse.Namespace) -> int:
    """Mark near-duplicate articles, pointing dup_of at the canonical one."""
    from newsrag.corpus import dedup

    dedup.dedup(threshold=args.threshold)
    return 0


def _stats(args: argparse.Namespace) -> int:
    """Print the corpus health report (read-only)."""
    from newsrag.corpus import stats

    stats.stats()
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

    p_stats = sub.add_parser("stats", help="print a corpus health summary")
    p_stats.set_defaults(func=_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
