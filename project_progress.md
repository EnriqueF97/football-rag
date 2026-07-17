# Project Progress — Evaluation-First RAG over Energy News

> Living document. It is the single source of truth for **what is done, what is next,
> and why**. Update the checkboxes and the percentages as work lands. Do not delete
> completed items — the history is part of the story.

**How to read this file**
- `[ ]` not started · `[~]` in progress · `[x]` done
- Each phase has a **weight** (its share of the whole project) and its own **local %**.
- The overall % at the top is the weighted sum of the phase %s. It is recomputed by
  hand when a phase moves — the weights are chosen so the number tracks *real value
  delivered*, not lines of code (the golden set is small to write and large in weight).
- Phases are ordered by dependency. You cannot honestly start Phase 3 before Phase 1
  produces full article bodies. Phase 2 (golden set) can start as soon as the corpus
  exists and should — it defines everything downstream.

---

## Overall completion: **10%**

| # | Phase | Weight | Local % | Status |
|---|-------|:------:|:-------:|--------|
| 0 | Project scaffolding | 5% | 100% | ✅ done |
| 1 | Corpus construction (fix + harden the scraper) | 15% | ~33% | 🔨 in progress |
| 2 | Golden evaluation set | 20% | 0% | not started |
| 3 | Retrieval core (chunk · embed · index · hybrid) | 15% | 0% | not started |
| 4 | Generation + citations | 8% | 0% | not started |
| 5 | Evaluation harness | 17% | 0% | not started |
| 6 | Ablations + results table | 10% | 0% | not started |
| 7 | Cost & latency profiling | 5% | 0% | not started |
| 8 | Failure case + README writeup | 5% | 0% | not started |
| 9 | Stretch: agentic layer (LangGraph) | — | 0% | optional |

> Weights sum to 100% across phases 0–8. Phase 9 is genuine stretch and sits outside
> the denominator: shipping 0–8 well **is** the finished project.

---

## Phase 0 — Project scaffolding · weight 5% · **0%**

*Goal: a clean, reproducible Python project managed with `uv`, with git history from
commit one. This is the part a reader inspects first.*

- [x] **0.1** `git init`; first commit is the scaffold, not a data dump
- [x] **0.2** `uv init` + `pyproject.toml`; pin Python 3.13 (packaged w/ hatchling)
- [x] **0.3** `.gitignore` — excludes `*.db`, `01_data/`, HTML cache, `.env`, `.venv/`;
      `data/**` ignored except `data/golden/`. Verified with `git add --dry-run`.
- [x] **0.4** `.env.example` — names every secret (Anthropic key, etc.) with no values
- [x] **0.5** Package skeleton `src/newsrag/{corpus,rag,eval}`; `uv sync` runs clean
- [x] **0.6** `Makefile` stubs: corpus verbs route through the `corpus` CLI (fail loudly
      via NotImplementedError); `chunk`/`index`/`eval`/`ablations` echo their phase + exit 1
- [x] **0.7** Moved legacy notebooks to `notebooks/legacy/` and **git-ignored** them —
      local reference only (extract scraper logic from `00`–`04`, esp. `03`/`04`); the
      old thesis notebooks (`05`+) are not part of this project

> **Note:** committing is left to you (Enrique) — the working agreement keeps commits in
> your hands. Staging is clean: `git add -A` will not pick up the DB, cache, `.env`, or
> `.venv` (all ignored). Suggested first commit: `chore: project scaffold (Phase 0)`.

**Target layout** — one installable package `newsrag` (a single package avoids
shadowing the `eval` builtin and keeps imports clean; `corpus` is a console script):
```
pyproject.toml
Makefile
.env.example
src/newsrag/
  corpus/
    db.py       # schema + connection (from notebook 00)
    gdelt.py    # headline ingest (from notebook 03)
    fetch.py    # async fetch + on-disk HTML cache + robots.txt
    extract.py  # trafilatura + ftfy, full body (replaces the 3-paragraph scraper)
    dedup.py    # NEW — MinHash near-duplicate detection
    cli.py      # `corpus <subcommand>` (console script entry point)
  rag/          # phases 3–4
  eval/         # phase 5
data/golden/    # phase 2 — the ONE data artifact that IS committed
notebooks/legacy/  # original .ipynb scrapers — LOCAL reference only (git-ignored)
```

**Done when:** a fresh clone + `uv sync` gives a working env, and `make` lists the
targets. No data committed yet.

---

## Phase 1 — Corpus construction · weight 15% · **~33%** (1.1, 1.2 done)

*Goal: turn the legacy scraper (built to grab a 3-paragraph lede for a sentiment
score) into an ingest that produces **full, clean, de-duplicated article bodies** —
the hard requirement RAG imposes and the TFT never did.*

See `README.md` for why each of these is load-bearing for evaluation, not polish.

### 1.1 Schema migration (`db.py`) — ✅ *done*
- [x] **1.1.1** Ported + simplified the schema: only `articles` + `chunks` (dropped all
      the energy/TFT tables: liquidity, market_context, opec_events, eia_events,
      llm_features). Runnable via `corpus init`; verified schema/indexes with `sqlite3`.
- [x] **1.1.2** Added to `articles`: `body_full`, `content_hash`, `dup_of`,
      `extractor_version`, `fetched_at`; renamed `datetime`→`published_at`.
- [x] **1.1.3** `raw_html` cache lives in a **separate** SQLite DB
      (`data/cache/html.sqlite`), gzip-compressed, disposable — keeps the corpus DB
      small. Decision: SQLite (transactional, single-file, queryable) over loose files.
- [x] **1.1.4** Resume contract preserved: `body_full IS NULL` + a partial index
      (`idx_articles_pending`) to keep the resume scan cheap. The scan/commit-every-N
      loop itself lands in `fetch.py` (1.3).

### 1.2 Headline ingest (`gdelt.py`) — ✅ *done*
- [x] **1.2.1** Ported the GDELT weekly-sweep loop (httpx sync); `corpus ingest` runs it.
- [x] **1.2.2** Incremental dedup-by-URL via `INSERT OR IGNORE` on the UNIQUE `url`
      (DB-level; re-run appends only new). Smoke-tested: 100 fetched → 8 new.
- [x] **1.2.3** Query list + date range + rate-limit in `config/football_queries.yaml`
      — the domain knob. No code change to retarget.

### 1.3 Fetch layer (`fetch.py`) — *the unblocker* — *~1.5 days*
- [ ] **1.3.1** `httpx` async fetch with a **per-domain** semaphore (politeness is
      measured per host, not in aggregate)
- [ ] **1.3.2** On-disk HTML cache — never hit a server twice for the same URL
- [ ] **1.3.3** `robots.txt` check + respectful UA + backoff on 429/5xx
- [ ] **1.3.4** Resume-capable; drops the ~1.5h sequential run to minutes

### 1.4 Extraction (`extract.py`) — *the correctness fix* — *~1 day*
- [ ] **1.4.1** Replace `soup.find_all('p')[:3]` with `trafilatura` full-body extract
      (boilerplate removal, date/author, sane fallback)
- [ ] **1.4.2** Replace the latin-1↔utf-8 mojibake hack with `ftfy.fix_text()`
- [ ] **1.4.3** Keep + tune `is_valid_body` (length, keyword, press-release, caps)
- [ ] **1.4.4** Extraction runs off the HTML cache, so re-extracting with new rules
      costs seconds — extractor quality is itself an ablation knob

### 1.5 Near-duplicate detection (`dedup.py`) — *NEW, evaluation-critical* — *~1 day*
- [ ] **1.5.1** Shingle + MinHash (or SimHash) over bodies
- [ ] **1.5.2** Cluster near-dups (syndicated wire copies across domains)
- [ ] **1.5.3** Elect one canonical per cluster; mark the rest `dup_of`
- [ ] **1.5.4** Report cluster-size distribution — a corpus-health number for the README

### 1.6 Corpus stats & sanity (`cli.py stats`) — *~0.5 day*
- [ ] **1.6.1** Counts: total / with-body / valid / unique-after-dedup
- [ ] **1.6.2** Coverage over time (histogram by month) — check for gaps/spikes
- [ ] **1.6.3** Domain distribution — spot over-representation

**Done when:** `make ingest && make fetch && make extract && make dedup` rebuilds the
corpus from scratch, every valid article has a full clean body, near-dups are marked,
and `stats` prints a health summary. The DB stays out of git; the scripts reproduce it.

---

## Phase 2 — Golden evaluation set · weight 20% · **0%**

*Goal: ~150 questions with reference answers **and annotated relevant document IDs**.
The boring part nobody does; the entire thesis. Build it early — it constrains every
downstream choice.*

### 2.1 Design
- [ ] **2.1.1** Define question taxonomy: factual · numeric · temporal ·
      multi-doc · **unanswerable** (for abstention) · post-cutoff (anti-memory)
- [ ] **2.1.2** Define the relevance rubric — what counts as a "relevant" doc vs.
      merely "related". Write it down; annotators (you) must be consistent.
- [ ] **2.1.3** Define the golden-set JSON schema (question, answer, relevant_ids,
      type, difficulty, date_asof)

### 2.2 Draft (LLM-assisted, human-owned)
- [ ] **2.2.1** Sample candidate docs across time/domains for coverage
- [ ] **2.2.2** LLM proposes candidate Q/A pairs grounded in specific docs
- [ ] **2.2.3** **Human review every item** — this is the annotation, not a rubber stamp
- [ ] **2.2.4** Deliberately bias toward memory-proof questions (recent, numeric,
      specific) so the set measures *retrieval*, not the model's weights

### 2.3 Relevance annotation
- [ ] **2.3.1** For each question, annotate the relevant document ID(s)
- [ ] **2.3.2** Cross-check against near-dup clusters — a relevant doc's twins must be
      handled consistently or recall@k is measuring noise (ties Phase 1.5 to eval)

### 2.4 Contamination baseline
- [ ] **2.4.1** Run **closed-book** (no retrieval) over the whole set
- [ ] **2.4.2** Record the closed-book score as the contamination floor; report *lift*,
      not raw accuracy

**Done when:** `data/golden/golden_set.jsonl` exists, every item is human-reviewed,
relevance IDs are annotated, and the closed-book baseline is recorded. This file **is**
committed (our own questions, not third-party article text).

---

## Phase 3 — Retrieval core · weight 15% · **0%**

*Goal: the plain-Python retrieval core being judged as engineering. Framework-free,
legible, every knob an ablation switch.*

### 3.1 Chunking (`rag/chunk.py`)
- [ ] **3.1.1** Fixed-size chunker (token window + overlap)
- [ ] **3.1.2** Semantic chunker (embedding-boundary or sentence-cluster)
- [ ] **3.1.3** `chunks` table keyed to `article_id`, with char offsets for citation
- [ ] **3.1.4** Chunking strategy is a config flag, not a rewrite

### 3.2 Embeddings (`rag/embed.py`)
- [ ] **3.2.1** `sentence-transformers` encoder; cache vectors to disk
- [ ] **3.2.2** Batch + resume; embedding a 10k-doc corpus is not free

### 3.3 Vector store (`rag/index.py`)
- [ ] **3.3.1** Local store (FAISS or Chroma) — no cloud bill
- [ ] **3.3.2** Dense retriever: top-k nearest neighbours

### 3.4 Lexical + hybrid (`rag/retrieve.py`)
- [ ] **3.4.1** BM25 index (the léxico half — nails tickers, names, dates)
- [ ] **3.4.2** Hybrid fusion (weighted score or Reciprocal Rank Fusion)
- [ ] **3.4.3** Unified `retrieve(query, config)` → ranked chunks; config selects
      dense / BM25 / hybrid

### 3.5 Reranking (`rag/rerank.py`)
- [ ] **3.5.1** Cross-encoder reranker over the top-N candidates (the cascade)
- [ ] **3.5.2** Toggleable on/off — it is the headline ablation

**Done when:** `retrieve(query, config)` returns ranked chunks for every config
(dense · hybrid · hybrid+rerank), each behind one flag.

---

## Phase 4 — Generation + citations · weight 8% · **0%**

*Goal: grounded answers with verifiable citations. Anthropic models. This is small on
purpose — the retrieval and eval are the judged parts.*

- [ ] **4.1** Prompt template: answer **only** from context; cite chunk IDs; allowed to
      say "I don't know"
- [ ] **4.2** Generation via the Anthropic SDK (Batches API for eval-set cost control)
- [ ] **4.3** Parse citations back to chunk IDs → char offsets (enables citation-accuracy)
- [ ] **4.4** Thin CLI/Streamlit query path (§6 non-goal: do **not** over-build the UI)

**Done when:** `ask(question, config)` returns an answer + structured citations end to end.

---

## Phase 5 — Evaluation harness · weight 17% · **0%**

*Goal: one command turns the golden set into the README table. Retrieval and generation
measured **separately** so a failure names which half broke. Framework-free.*

### 5.1 Retrieval metrics (`eval/retrieval.py`)
- [ ] **5.1.1** recall@k, MRR, nDCG against annotated relevant IDs
- [ ] **5.1.2** Dup-aware credit: a retrieved twin of a relevant doc counts correctly
      (consumes Phase 1.5 + 2.3.2)

### 5.2 Generation metrics (`eval/generation.py`)
- [ ] **5.2.1** Groundedness / faithfulness — is each claim supported by *retrieved*
      context (hallucination detection, not answer-similarity)
- [ ] **5.2.2** Citation accuracy — does the cited chunk actually support the claim
- [ ] **5.2.3** Correctness via **LLM judge** vs. reference answer

### 5.3 Judge validation — *the differentiator* (`eval/judge.py`)
- [ ] **5.3.1** Human-annotate a subset; measure judge↔human agreement (κ / % agree)
- [ ] **5.3.2** Optional cross-model judge for a second opinion
- [ ] **5.3.3** Report agreement in the README — an LLM judge you never validated is
      not evidence. This is the Hammer skill, transferred.

### 5.4 Abstention metric (`eval/abstention.py`)
- [ ] **5.4.1** On the unanswerable subset, score correct "I don't know" vs. confident
      wrong answers

### 5.5 Runner (`eval/run.py`)
- [ ] **5.5.1** `make eval` runs a config end to end, writes metrics JSON
- [ ] **5.5.2** Deterministic + reproducible (seeded, cached where safe)

**Done when:** `make eval CONFIG=hybrid_rerank` prints separated retrieval + generation
numbers and writes a machine-readable result. Regenerating results is one command, not
manual — otherwise the ablations rot.

---

## Phase 6 — Ablations + results table · weight 10% · **0%**

*Goal: the README table. Show what moved the needle **and what didn't**.*

- [ ] **6.1** Sweep configs: dense-only · hybrid · hybrid+rerank
- [ ] **6.2** Sweep chunking: fixed vs. semantic
- [ ] **6.3** Aggregate all runs into one Markdown table, auto-generated
- [ ] **6.4** Write the interpretation: which knob paid off, which didn't, hypothesis why
      (negative results are credible — do not hide them)

**Done when:** `make ablations` regenerates the full comparison table from scratch.

---

## Phase 7 — Cost & latency profiling · weight 5% · **0%**

*Goal: the number product teams ask first and almost nobody reports.*

- [ ] **7.1** Instrument token counts per query per config
- [ ] **7.2** €/1k queries per config
- [ ] **7.3** p50 / p95 latency per config
- [ ] **7.4** Fold cost + latency columns into the Phase 6 table (accuracy is not free)

**Done when:** every row of the results table carries its cost and latency.

---

## Phase 8 — Failure case + README writeup · weight 5% · **0%**

*Goal: the 90-second read. README leads with the results table and the "why", plus one
honest documented failure. Reads senior, not weak — do not sand it off.*

- [ ] **8.1** Find and document one real failure (where it breaks and why)
- [ ] **8.2** Write the data-provenance section (copyright reasoning — itself a signal)
- [ ] **8.3** README leads with the finding, not install steps
- [ ] **8.4** Verify the Definition-of-Done paragraph (AGENTS.md §8) can be written
      **truthfully** with the real numbers — if not, the project isn't done

**Done when:** a hiring manager reading for 90 seconds gets the finding, the honest
caveat, and the reproduce command.

---

## Phase 9 — Stretch: agentic layer (LangGraph) · optional · **0%**

*Only after 0–8 are solid. LangChain/LangGraph for orchestration where it earns its
place — not a wrapper around the core.*

- [ ] **9.1** Agent decides whether to retrieve at all
- [ ] **9.2** Query reformulation on weak retrieval
- [ ] **9.3** Explicit "I don't know" path; measure abstention lift vs. Phase 5.4

---

## Working agreement

- **Part by part.** Each subtask is reviewable on its own. No "run the whole notebook
  and trust it." When a component lands, its purpose is stated before moving on.
- **`uv` for all Python dependency management.** No bare `pip install`.
- **Small, readable commits.** The git history is part of the portfolio (AGENTS.md §7).
- **Evaluation beats features.** Every tradeoff resolves toward rigor (AGENTS.md §2).
- **No third-party article text in git.** URLs, IDs, embeddings, snippets, and the
  scripts to rebuild — never the bodies (AGENTS.md §3).
