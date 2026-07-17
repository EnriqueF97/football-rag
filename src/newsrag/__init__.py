"""Evaluation-first RAG over public news.

Domain: football/soccer (top-5 European leagues + Champions League). The domain is a
configuration knob — the GDELT query list — so the same machinery retargets to F1,
cybersecurity, or anything else.

Subpackages:
    corpus  — ingest, fetch, extract, dedup the news corpus (Phase 1)
    rag     — chunk, embed, index, retrieve, rerank, generate (Phases 3-4)
    eval    — the evaluation harness: golden set metrics + judge (Phase 5)
"""

__version__ = "0.1.0"
