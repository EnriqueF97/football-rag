# Evaluation-First RAG over Football News

> A retrieval-augmented QA system over a public corpus of football/soccer news —
> built to be **measured, not demoed**. The deliverable is not "a RAG repo." The
> deliverable is a rigorous evaluation harness that can say **which half of the system
> works, by how much, and at what cost.**
>
> The domain is a **configuration knob** (`config/football_queries.yaml`), so the same
> machinery retargets to F1, cybersecurity, or anything else without touching the core.

**Corpus today:** 26,378 usable articles · 2024-08 → 2026-06 · 73% reliable / 27% tabloid.
The tabloid tier is deliberate — conflicting coverage of transfer sagas is what makes
questions about synthesis, temporal resolution, and source reliability possible.

📄 The live build plan, with per-phase completion, lives in
[`project_progress.md`](project_progress.md). This README is the *why* and the *what*;
that file is the *where we are*.

---

## What this is, and the problem it solves

**Two things get built here, and the second is the bigger half.**

1. **A question-answering system** over ~26k football articles. Ask *"why did club X
   dismiss its manager?"* and it finds the relevant articles, feeds them to an LLM, and
   answers with citations.
2. **A measuring apparatus** that says how good #1 actually is.

Without #2, every question that matters on day two is a guess:

- I changed the chunk size from 300 to 500 words — better or worse?
- A cross-encoder reranker costs 3× the latency. Is it worth it?
- This answer came back wrong. **Why?**
- It answered correctly about a famous transfer — from *my corpus*, or from what the model
  already knew?

### Which half failed?

A RAG system is two stages in series:

```
question ──► [1. RETRIEVAL] ──► top-k chunks ──► [2. GENERATION] ──► answer
```

When the answer is wrong there are two suspects, and they need **opposite** fixes:

| Retrieval failed | Generation failed |
|---|---|
| The right article was never found | The right article **was** found |
| The LLM got text that didn't contain the answer | The LLM misread it, or invented something |
| It never had a chance | It had everything and still missed |
| Fix: chunking, embeddings, hybrid search | Fix: prompt, model, context budget |

A single end-to-end accuracy number cannot tell these apart — so you spend weeks
optimising the wrong half. Measuring them **separately** is the core engineering claim.

There is also a third, sneakier case: retrieval fails but the answer is *right*, because
the model answered from memory rather than from the corpus. That looks like success and is
a failure — it doesn't generalise to new information and can't be audited. The
**closed-book baseline** (same questions, no retrieval) exists to measure exactly that.

### What makes the diagnosis possible

The golden set: ~150 hand-written questions, each with **three** fields.

```
question:       "Why did club X dismiss its manager?"
reference:      "Six games without a win and a fallout with the board."
relevant docs:  [art_4471, art_4488, art_5012]   ← the field almost nobody writes
```

That third field is the instrument. Because we know the answer lives in article 4471, we
can ask *"did retrieval return it?"* — independently of whatever the LLM then wrote. That
question is `recall@k`, and it is what splits the diagnosis into two halves.

### What the project produces

The same 150 questions, run through different configurations:

| Config | recall@5 | Verdict |
|---|---:|---|
| dense embeddings only | 0.71 | baseline |
| + BM25 hybrid | 0.83 | helped |
| + cross-encoder rerank | 0.89 | helped — at 3× latency |
| + semantic chunking | 0.88 | **did not help** |

*(Illustrative shape only — the real numbers are produced in Phase 6.)*

**That table is the deliverable, not the chatbot.** Reporting what did *not* help is what
makes the rest credible.

### The vocabulary, in one line each

| Term | What it is |
|---|---|
| **Chunk** | A slice of an article (~200–400 words). We retrieve slices, not whole articles — a paragraph answers the question; 2,000 words dilute it and cost tokens. |
| **Embedding** | A text turned into a few hundred numbers representing its *meaning*. Similar meanings land close together, so search becomes geometry. Matches "why was the coach fired?" to "the club dismissed its manager" with zero shared words. |
| **BM25** | Classic lexical search (weighted keyword matching). Blind to meaning, but exact on names, fees and dates — precisely where embeddings fumble. |
| **Hybrid retrieval** | BM25 + embeddings together. Each covers the other's blind spot. Football is entity-dense (players, clubs, fees), so this ablation should show a clear, explainable win. |
| **Cross-encoder reranker** | A slower, more accurate model that reads question and chunk *together*. Too expensive to run over the whole corpus, so it re-ranks the top ~50 candidates a cheap retriever found. |
| **Groundedness** | Is each claim in the answer actually supported by the retrieved text — or invented? Hallucination detection under another name. |
| **Abstention** | Answering "I don't know" when the corpus can't support an answer. A real evaluation dimension most projects can't measure. |

---

## Repository map — where everything lives

```
rag-football/
├── AGENTS.md                 charter + operating manual — READ FIRST, kept always-current
├── README.md                 what the system is and how it's measured (this file)
├── project_progress.md       phase-by-phase plan + % completion — "where we are"
├── Makefile                  one-line verb per pipeline step
├── pyproject.toml            deps (via uv) + the `corpus` console script
├── docs/
│   └── commands.md           every command, with each flag and value explained
├── config/
│   └── football_queries.yaml THE DOMAIN KNOB — queries, source allowlist, validity rules
├── data/                     git-ignored except golden/
│   ├── corpus.db             the corpus: articles + chunks + ingest_log
│   ├── cache/html.sqlite     gzipped raw HTML — disposable, never re-downloaded
│   └── golden/               the ONE committed data artifact (Phase 2)
└── src/newsrag/
    ├── corpus/               ── Phase 1: build the corpus ── ✅ complete
    │   ├── db.py             schema, connections, idempotent migrations
    │   ├── guardian.py       Guardian API ingest — full bodies, no scraping
    │   ├── cc.py             Common Crawl discovery + WARC fetch (tabloid tier)
    │   ├── mediacloud_ingest.py  Media Cloud discovery (BBC/Sky)
    │   ├── gdelt.py          GDELT ingest — BENCHED (IP-throttled; kept for reference)
    │   ├── fetch.py          async HTML fetch: robots.txt, per-domain concurrency caps
    │   ├── extract.py        HTML → clean body (trafilatura+ftfy) + validity filter
    │   ├── dedup.py          MinHash + LSH near-duplicate clustering → `dup_of`
    │   ├── stats.py          health funnel, loss accounting, Phase-2 readiness gates
    │   ├── smell.py          per-document anomaly battery (catches what gates miss)
    │   └── cli.py            the `corpus` command (all subcommands)
    ├── rag/                  ── Phases 3–4: chunk · embed · index · retrieve · generate
    └── eval/                 ── Phase 5: golden set · metrics · validated judge
```

`rag/` and `eval/` are scaffolded but empty — that is the work ahead.

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
  Guardian API ───► full bodies, no scraping ──┐
  Common Crawl ───► WARC byte-ranges (tabloid) ├─► fetch ─► extract ─► dedup
  Media Cloud  ───► discovery (BBC/Sky)      ──┘  (robots,  (trafila-   (MinHash
                                                   cached)   tura+ftfy)  +LSH)
                                                              │
                                        corpus (SQLite, full bodies, near-dups marked)
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

### The retrieval pipeline, mapped to the code that implements it

The pipeline runs in two distinct times. **Indexing happens once**, before any question
exists; **retrieval happens per question**, against what indexing precomputed.

**Offline — once (`make chunk && make index`)**

| Step | What it actually does | Module | Plan | Status |
|---|---|---|---|:--:|
| Chunk | Split ~26k articles into ~65k slices of ~300 words, keeping char offsets so a citation can point at the exact passage | `rag/chunk.py` | 3.1 | ⬜ |
| Embed | Turn each chunk into a ~384-number vector with sentence-transformers; cache to disk (encoding 65k chunks is not free) | `rag/embed.py` | 3.2 | ⬜ |
| Index | Store the vectors in FAISS for nearest-neighbour search, and build the BM25 word-statistics index alongside | `rag/index.py` | 3.3 | ⬜ |

**Per question**

| Step | What it actually does | Module | Plan | Status |
|---|---|---|---|:--:|
| Retrieve | Embed the question into the *same* vector space and take nearest chunks (dense); score the same chunks by rare-word overlap (BM25); fuse the two rankings with RRF | `rag/retrieve.py` | 3.4 | ⬜ |
| Rerank | Cross-encoder reads (question, chunk) *together* over the top ~50 candidates and reorders them → top 5. Too slow for 65k chunks, hence the cascade | `rag/rerank.py` | 3.5 | ⬜ |
| Generate | Paste the top chunks + their IDs into the prompt; answer only from that context, cite IDs, may say "I don't know" | `rag/generate.py` | 4 | ⬜ |

```
65,000 chunks ──[dense + BM25, cheap]──► ~50 candidates ──[reranker, costly]──► 5 → LLM
```

Nothing in retrieval "understands" the question: it computes distances and counts rare
words, then returns the best-scoring slices. That is exactly why it fails in unintuitive
ways, and why every arrow above is a switch in the ablation table — chunk size, dense vs.
hybrid, reranker on/off.

Granular sub-tasks and completion live in [`project_progress.md`](project_progress.md).

**Design rule — where frameworks are allowed:**

| Part | Framework | Why |
|---|---|---|
| Retrieval core | **none** — plain Python | This is the part judged as engineering, and the part worth understanding |
| Evaluation harness | **none** — plain Python | The thesis of the project; it must be legible |
| Generation orchestration | **LangChain**, sparingly | Only where it genuinely earns its place |
| Agentic layer (stretch) | **LangGraph** | It needs *cycles* — reformulate a weak query and retry, decide whether to retrieve at all, abstain. A linear chain cannot do that. |

A repo that is a pure framework wrapper reads as not knowing what sits underneath.

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

Sources are public and licensing-safe — mainstream football coverage via the **Guardian
Open Platform API**, **Common Crawl**, and **Media Cloud**. The fetcher honours each
domain's `robots.txt` and caps concurrency per host.

The one committed data artifact is `data/golden/` — our own questions and annotations,
not anyone's article text.

---

## Reproduce

```bash
uv sync                       # environment
make init                     # create the SQLite databases

make ingest-guardian          # Guardian API → metadata + full bodies
make ingest-cc && make fetch-cc   # Common Crawl: discover, then fetch WARC ranges
make ingest-mediacloud        # Media Cloud: discover BBC/Sky URLs
make fetch                    # article HTML → cache (async, robots-aware)

make extract                  # cache → full clean bodies
make dedup                    # mark near-duplicate copies
make stats && make smell      # health gates + per-document anomaly checks

make chunk && make index      # chunk + embed + build indexes      [Phase 3]
make eval CONFIG=hybrid_rerank   # golden set → separated metrics  [Phase 5]
make ablations                # comparison table (+ cost/latency)  [Phase 6]
```

Ingest steps are resumable — an `ingest_log` records completed windows, so an interrupted
run continues instead of re-requesting. Needs `GUARDIAN_API_KEY` and `MEDIACLOUD_API_KEY`
in `.env` (see `.env.example`).

Every number in the README table regenerates from these commands. If regenerating is
manual, the results rot — so it isn't.
