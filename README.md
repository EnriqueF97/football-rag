# Evaluation-First RAG over Football News

> A retrieval-augmented QA system over a public corpus of football/soccer news —
> built to be **measured, not demoed**. The deliverable is not "a RAG repo." The
> deliverable is a rigorous evaluation harness that can say **which half of the system
> works, by how much, and at what cost.**
>
> The domain is a **configuration knob** (the GDELT query list), so the same machinery
> retargets to F1, cybersecurity, or anything else without touching the core.

📄 The live build plan, with per-phase completion, lives in
[`project_progress.md`](project_progress.md). This README is the *why* and the *what*;
that file is the *where we are*.

---

## What we are actually measuring

Not "how similar is the answer to the reference." Lexical similarity (ROUGE/BLEU/cosine)
rewards fluent hallucinations and punishes correct rephrasings — it is precisely the bad
evaluation this project exists to avoid. Instead:

| Dimension | Metric | The question it answers |
|-----------|--------|-------------------------|
| **Retrieval** | recall@k, MRR, nDCG | Did we find the evidence at all? |
| **Groundedness** | claim ↔ retrieved context | Did it invent this, or read it? |
| **Citation accuracy** | citation ↔ cited chunk | Does the pointer point where it claims? |
| **Correctness** | answer ↔ reference, **validated LLM judge** | Was it right? |
| **Abstention** | "I don't know" on unanswerable Qs | Does it know when it doesn't know? |

Retrieval and generation are scored **separately on purpose**: a single end-to-end
number tells you the system failed but not *which half*. That split is the core
engineering claim.

Two measurements almost nobody reports, and both are here:
- **Closed-book baseline** — the same questions with no retrieval. Whatever the model
  answers from memory is the *contamination floor*; we report the **lift** over it, not
  raw accuracy.
- **Judge validation** — agreement between the LLM judge and human annotation. An
  unvalidated judge is not evidence.

---

## Architecture

```
                 ┌── ingest (GDELT headlines) ──┐
  public news ──►│  fetch (async, cached, robots)│──► corpus (SQLite, full bodies,
                 └── extract (trafilatura+ftfy) ─┘     near-dups marked)
                                                              │
                        ┌──── chunk (fixed | semantic) ───────┘
                        ▼
   embed ──► vector store (FAISS/Chroma) ─┐
                                          ├─► hybrid retrieve ──► cross-encoder rerank
   BM25 index ────────────────────────────┘                              │
                                                                          ▼
                              question ──────────────────────► top-k chunks ──► LLM
                                                                          │      (grounded,
                                                                          ▼       cited,
                                              ┌──────── EVAL HARNESS ─────┤       can abstain)
                                              │  retrieval metrics        │
   golden set (150 Qs, refs, relevant IDs) ──►│  generation metrics       │──► README table
                                              │  validated LLM judge      │    (+ cost/latency)
                                              └───────────────────────────┘
```

**Design rule (AGENTS.md §5):** LangChain/LangGraph orchestrates *only where it earns
its place*. The retrieval core and the **entire eval harness are plain, framework-free
Python** — that is the part being judged as engineering.

---

## Tools

| Concern | Choice | Why |
|---------|--------|-----|
| Dependency mgmt | **`uv`** | Fast, reproducible, single lockfile. All Python deps go through it — no bare `pip`. |
| Language | **Python 3.13** | — |
| Corpus store | **SQLite** | One deterministic file; the pipeline is a join, not a pile of CSVs. |
| Fetch | **`httpx` (async)** | Per-domain politeness + on-disk HTML cache; minutes, not the legacy ~1.5h. |
| Extraction | **`trafilatura` + `ftfy`** | Full clean body (not the legacy 3-paragraph lede) with encoding repair. |
| Dedup | **MinHash / SimHash** | Kills syndicated wire copies that would corrupt recall@k. |
| Embeddings | **sentence-transformers** | Local, no cloud bill. |
| Vector store | **FAISS / Chroma** | Local. |
| Reranker | **cross-encoder** | The headline retrieval ablation. |
| Generation & judge | **Anthropic SDK** (Batches API) | Known quantity; Batches keeps eval-set cost down. |
| Orchestration (stretch) | **LangChain / LangGraph** | Only for the agentic layer. |

---

## Data provenance & copyright

**No third-party article text in git.** News bodies are the outlets' copyright — that
doesn't change based on who wrote the scraper. The repo stores URLs, IDs, embeddings, and
short snippets; the ingest scripts rebuild the corpus so a reader regenerates it
themselves rather than us republishing anyone's content. The SQLite corpus DB and the
HTML cache are `.gitignore`d.

Sources are public and licensing-safe — mainstream football coverage indexed by **GDELT**
(BBC Sport, ESPN, The Guardian, and similar). The fetcher honours each domain's
`robots.txt` and caps concurrency per host.

The one committed data artifact is `data/golden/` — our own questions and annotations,
not anyone's article text.

---

## Reproduce

```bash
uv sync                       # environment
make ingest                   # GDELT headlines → DB
make fetch                    # article HTML → cache (async, robots-aware)
make extract                  # cache → full clean bodies
make dedup                    # mark near-duplicate wire copies
make chunk && make index      # chunk + embed + build indexes
make eval CONFIG=hybrid_rerank   # golden set → separated metrics
make ablations                # full comparison table (+ cost/latency)
```

Every number in the README table regenerates from these commands. If regenerating is
manual, the results rot — so it isn't.

---

## Non-goals (AGENTS.md §6)

A polished chat UI · fine-tuning · scale for its own sake · beating SOTA. A 10k-document
corpus that is *well-evaluated* beats a million-document corpus that isn't.
