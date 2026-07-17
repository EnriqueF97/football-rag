"""Retrieval core and generation (Phases 3-4).

Kept deliberately framework-free: chunking, embeddings, vector store, BM25, hybrid
fusion, and cross-encoder reranking in plain Python — this is the part judged as
engineering. LangChain/LangGraph only enters in the stretch agentic layer (Phase 9).
"""
