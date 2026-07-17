"""The evaluation harness (Phase 5) — the thesis of the project.

Retrieval and generation are scored separately against the golden set, so a failure
names which half broke. Includes the validated LLM judge (agreement vs. human
annotation), the closed-book contamination baseline, and the abstention metric.
"""
