"""Corpus construction: ingest headlines, fetch HTML, extract bodies, mark near-dups.

Reworks the legacy GDELT notebooks (notebooks/legacy/00, 03, 04) into a package that
produces full, clean, de-duplicated article bodies — the hard requirement RAG imposes
and the original sentiment pipeline never did.
"""
