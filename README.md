# Evaluation-First RAG over Energy News

> A retrieval-augmented QA system over a public corpus of energy/commodity news —
> built to be **measured, not demoed**. The deliverable is not "a RAG repo." The
> deliverable is a rigorous evaluation harness that can say **which half of the system
> works, by how much, and at what cost.**

📄 The live build plan, with per-phase completion, lives in
[`project_progress.md`](project_progress.md). This README is the *why* and the *what*;
that file is the *where we are*.

---

## The thesis in one paragraph

The noise floor for RAG portfolio projects is enormous: ingest a PDF, drop it in a
vector store, call an LLM, teach the reader nothing — because no hard decision was ever
made inside. This project makes the hard decision the whole point. Almost no RAG project
has an **evaluation** story: a held-out golden set, retrieval and generation scored
separately, an LLM judge that was itself validated against human annotation, and cost
/ latency reported per configuration. That gap is the opportunity, and evaluation rigor
is the entire thesis. **Whenever "more features" fights "more rigorous evaluation,"
evaluation wins.**

## Why energy news, and why not a PDF

A temporally-structured corpus enables what a static document cannot: time-filtered
retrieval, conflicting versions of a developing story, facts that expire. That is where
the interesting failures live — and it is also what exposes the **memorization trap**:
a model can answer a question correctly *from its own weights* while the retrieval
underneath is broken. A news corpus with timestamps lets us build memory-proof
questions and catch that failure. A PDF hides it.

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

## What "done" looks like

The project is finished when this paragraph can be written **truthfully** from real
numbers (AGENTS.md §8):

> Hybrid retrieval + cross-encoder reranking improved recall@5 from X to Y on a
> 150-question golden set, at Z× the latency and €N/1k queries. Semantic chunking did
> not help, and here is our hypothesis for why. The system abstains correctly on P% of
> unanswerable questions. Here is a case where it still fails.

If that paragraph can't be written honestly, the project isn't done — regardless of how
much code exists.

---

## Data provenance & copyright

Reusing the author's own GDELT scraping machinery from a prior internship is cleared and
settled — the ingest, the domain, and the signal approach transfer. Two rules hold
regardless of who owns the scraper:

1. **No third-party article text in git.** News bodies are the outlets' copyright. The
   repo stores URLs, IDs, embeddings, and short snippets; a script rebuilds the corpus
   so a reader regenerates it themselves. The SQLite DB and HTML cache are `.gitignore`d.
2. **Nothing employer-specific** — no internal signal definitions, client logic,
   proprietary data, or credentials. We reuse the machinery, not the business.

Sources are public and licensing-safe: **GDELT** (default), **EIA open data**,
**CC-NEWS**, permissive **RSS**. Reuters/Bloomberg content is avoided.

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
