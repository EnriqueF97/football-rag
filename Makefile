# Project verbs. Each target is one command that rebuilds a step of the pipeline.
# Targets that route through `corpus` fail loudly (NotImplementedError) until their
# Phase 1 logic lands; the rest echo their plan phase. See project_progress.md.

.PHONY: help init ingest ingest-guardian ingest-mediacloud ingest-cc fetch-cc fetch extract dedup stats smell chunk index eval ablations

help:
	@echo "Corpus (Phase 1):"
	@echo "  make init      create the SQLite databases (schema only)"
	@echo "  make ingest    GDELT headlines -> DB (deprecated: IP-throttled)"
	@echo "  make ingest-guardian  Guardian API: metadata -> DB, bodies -> cache"
	@echo "  make fetch     article HTML -> cache (async, robots-aware)"
	@echo "  make extract   cache -> full clean bodies"
	@echo "  make dedup     mark near-duplicate wire copies"
	@echo "  make stats     corpus health summary"
	@echo "  make smell     per-document anomaly checks (usable set)"
	@echo "Retrieval / eval (Phases 3-6):"
	@echo "  make chunk     split bodies into chunks            [not implemented]"
	@echo "  make index     embed + build vector/BM25 indexes   [not implemented]"
	@echo "  make eval CONFIG=<dense|hybrid|hybrid_rerank>      [not implemented]"
	@echo "  make ablations full comparison table               [not implemented]"

# --- Phase 1: corpus (route through the CLI) ---
init:
	uv run corpus init

ingest:
	uv run corpus ingest

# --env-file: GUARDIAN_API_KEY lives in .env (gitignored)
ingest-guardian:
	uv run --env-file .env corpus ingest-guardian

# --env-file: MEDIACLOUD_API_KEY lives in .env (gitignored)
ingest-mediacloud:
	uv run --env-file .env corpus ingest-mediacloud

ingest-cc:
	uv run corpus ingest-cc

fetch-cc:
	uv run corpus fetch-cc

fetch:
	uv run corpus fetch

extract:
	uv run corpus extract

dedup:
	uv run corpus dedup

stats:
	uv run corpus stats

smell:
	uv run corpus smell

# --- Phases 3-6: not implemented yet, fail loudly ---
chunk:
	@echo "not implemented yet — Phase 3.1 (project_progress.md)"; exit 1

index:
	@echo "not implemented yet — Phase 3.2-3.4 (project_progress.md)"; exit 1

CONFIG ?= hybrid_rerank
eval:
	@echo "not implemented yet — Phase 5 (project_progress.md); CONFIG=$(CONFIG)"; exit 1

ablations:
	@echo "not implemented yet — Phase 6 (project_progress.md)"; exit 1
