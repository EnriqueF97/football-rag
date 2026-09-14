# AGENTS.md — Charter & Operating Manual

**If you are a new agent session: read this file completely before doing anything.** It
is the single source of truth for what this project is, why it exists, what data and code
already exist, and what is still to be built.

---

## ⚠️ THE STANDING RULE — keep this file true

**Any change to the plan, or to what has been done, MUST be written back into this file
in the same working session that makes the change. ALWAYS. No exceptions.**

This is not documentation hygiene — it is the contract that makes a fresh agent session
useful. A stale AGENTS.md actively causes damage: it sends the next session to work on a
false premise. (This has already happened once: both this file and the README kept
claiming the corpus was ingested via GDELT for weeks after GDELT had been dropped
entirely.)

Update this file when **any** of these happen:

| Trigger | What to update |
|---|---|
| A phase or sub-task is completed | §4 status/roadmap, §6 code inventory |
| A design decision is made or reversed | The relevant section + §9 if it becomes a caveat |
| New code is written | §6 (move it out of §7) |
| The corpus changes (re-ingest, cleanup, new source) | §5 — **re-query the DB, do not guess** |
| A tool/source is dropped or added | §8, §6, and `README.md` |
| A gate fails and is accepted anyway | §9, with the reasoning and who decided |
| An operational lesson is learned the hard way | §10 |

When updating §5, **run the queries in that section against the live DB**. Numbers in
this file must be measured, never remembered or estimated. Date-stamp what you verify.

Companion documents (keep consistent with this one):
- **`WORKING_AGREEMENT.md` — how to collaborate and the code-quality bar. Read it before
  writing code; it is project-agnostic and it overrides §3 here if they ever disagree.**
- `project_progress.md` — the granular phase/sub-task checklist and % completion.
- `README.md` — what the system is, how it's measured, and the repository map.
- `docs/commands.md` — every command with its flags explained.

---

## 1. What is being built

**Two things get built here, and the second is the bigger half.**

1. **A question-answering system** over ~26k football articles. Ask *"why did club X
   dismiss its manager?"* and it finds the relevant articles, feeds them to an LLM, and
   answers with citations.
2. **A measuring apparatus** that says how good #1 actually is.

Building #1 is maybe 30% of the work. #2 is the rest, and it is the point.

### Why #2 exists

Without measurement, every question that matters on day two is a guess:

- The chunk size changed from 300 to 500 words — better or worse?
- A cross-encoder reranker costs 3× the latency. Is it worth it?
- This answer came back wrong. **Why?**
- It answered correctly about a famous transfer — from *the corpus*, or from what the
  model already knew?

Those are the actual daily questions on a real team. Answering them requires an
instrument, not an opinion.

### "Which half failed" — the central idea

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

A single end-to-end accuracy number cannot separate these, so you optimise the wrong half
for weeks. Measuring retrieval and generation **separately** is the core engineering claim
of this project.

There is a third, sneakier case: retrieval fails but the answer is *right*, because the
model answered from memory rather than from the corpus. That looks like success and is a
failure — it doesn't generalise to new information and can't be audited. The
**closed-book baseline** (same questions, no retrieval) measures exactly that, and we
report the *lift* over it rather than raw accuracy.

### What makes the diagnosis possible

The golden set (Phase 2): ~150 hand-written questions, each with **three** fields.

```
question:       "Why did club X dismiss its manager?"
reference:      "Six games without a win and a fallout with the board."
relevant docs:  [art_4471, art_4488, art_5012]   ← the field almost nobody writes
```

The third field is the instrument. Because we know the answer lives in article 4471, we
can ask *"did retrieval return it?"* — independently of whatever the LLM then wrote. That
question is `recall@k`, and it is what splits the diagnosis into two halves.

### What the project ultimately produces

The same 150 questions, run through different configurations:

| Config | recall@5 | Verdict |
|---|---:|---|
| dense embeddings only | 0.71 | baseline |
| + BM25 hybrid | 0.83 | helped |
| + cross-encoder rerank | 0.89 | helped — at 3× latency |
| + semantic chunking | 0.88 | **did not help** |

*(Illustrative shape only — real numbers are produced in Phase 6.)*

**That table is the deliverable, not the chatbot.** Reporting what did *not* help is what
makes the rest credible.

**If a tradeoff ever pits "more features" against "more rigorous evaluation", evaluation
wins** — because without measurement, new features are faith.

## 2. Why — what the user wants to learn

Enrique Favila. Software engineering background. He is building this **to learn**, not to
ship a product. The artifact matters less than the skills, which must be strong enough to
defend in an interview and reuse at work:

- **How RAG works end to end**, not as a framework wrapper: chunking, embeddings, vector
  stores, BM25, hybrid fusion, cross-encoder reranking — and *why each exists*.
- **How to evaluate an LLM system with rigor**: a human-annotated golden set, retrieval
  and generation scored separately, an LLM judge that is itself validated against human
  annotation, a closed-book contamination baseline, and abstention ("I don't know").
- **Cost and latency profiling** — the first thing a product team asks, and almost nobody
  reports.
- **Enough LangChain / LangGraph** to orchestrate, used sparingly and knowingly — never as
  a black box he can't explain.
- **Good engineering habits**: reproducible pipeline (`uv`, one-command eval), clean git
  history.

### Working language and level
See `WORKING_AGREEMENT.md` §1. The project-specific part: the concepts to teach on first
use here are RAG/IR ones — recall@k, nDCG, cross-encoder, BM25, MinHash, groundedness.

## 3. How to work with him (this matters most)

📌 **Read [`WORKING_AGREEMENT.md`](WORKING_AGREEMENT.md) before writing any code.** It is
the authority on collaboration style, the code-quality bar, and what is his call rather
than yours. It is deliberately project-agnostic so he can reuse it elsewhere — do not
duplicate its contents here, and if the two ever conflict, it wins.

The one thing worth repeating because it is easy to violate by reflex:

> **He writes the implementations. You scaffold and review.** Signatures, docstrings, step
> comments and hints are yours; function bodies are his. Write a full implementation only
> when he explicitly asks. Review like a supervisor sitting beside him: point at the line,
> name why it matters, suggest a direction — never silently rewrite his code.

Project-specific additions to that agreement:
- **Keep `project_progress.md` current** (checkboxes + %), and **this file** (see the
  standing rule above).

## 4. Where we are — status and roadmap

**Overall completion: 20%.** Phase 1 (corpus) finished 2026-07-22.

| # | Phase | Weight | Status |
|---|---|:---:|---|
| 0 | Project scaffolding | 5% | ✅ done |
| 1 | Corpus construction | 15% | ✅ done — 26,378 usable articles |
| 2 | **Golden evaluation set** | 20% | ⬜ **NEXT — the biggest single phase** |
| 3 | Retrieval core (chunk · embed · index · hybrid) | 15% | ⬜ not started |
| 4 | Generation + citations | 8% | ⬜ not started |
| 5 | Evaluation harness | 17% | ⬜ not started |
| 6 | Ablations + results table | 10% | ⬜ not started |
| 7 | Cost & latency profiling | 5% | ⬜ not started |
| 8 | Failure case + README writeup | 5% | ⬜ not started |
| 9 | Stretch: agentic layer (LangGraph) | — | optional |

### Definition of done
The README can **truthfully** state the ablation results, the abstention rate, and the
failure case — with every number regenerated by a single command. If that paragraph can't
be written honestly, the project isn't finished, no matter how much code exists.

### Deliverables, in priority order
1. A **human-annotated golden set** (~150 questions) — the boring, hard part almost nobody
   does, which is exactly why it is the signal.
2. **Retrieval and generation measured separately** — recall@k / MRR / nDCG for retrieval;
   groundedness and citation accuracy for generation — so a failure names *which half*
   broke.
3. **Ablations with a results table**: dense vs. hybrid, reranking on/off, fixed vs.
   semantic chunking. Report what helped *and what didn't*.
4. **Cost and latency per configuration**: tokens, cost/1k queries, p50/p95.
5. **One honest, documented failure case.**

## 5. The data we have (verified 2026-09-04 against `data/corpus.db`)

> Re-run these queries before trusting these numbers. They are a snapshot, not live state.

**Headline: 25,215 usable articles**, full clean bodies, deduplicated, spanning
**2024-08 → 2026-06 across 23 months** of even coverage.

```bash
sqlite3 data/corpus.db "SELECT COUNT(*) total, SUM(body_full IS NOT NULL) with_body,
  SUM(body_valid=1 AND dup_of IS NULL) usable FROM articles;"
# → 33594 | 28941 | 25215   (verified 2026-09-13, after the LLM-audit cleanup)
```

### Source mix — deliberate, not accidental
| Domain | Tier | Usable | Share |
|---|---|---:|---:|
| theguardian.com | reliable | 10,584 | 42.0% |
| bbc.co.uk | reliable | 7,845 | 31.1% |
| football365.com | tabloid | 1,977 | 7.8% |
| 90min.com | tabloid | 1,847 | 7.3% |
| teamtalk.com | tabloid | 1,641 | 6.5% |
| skysports.com | reliable | 806 | 3.2% |
| fourfourtwo.com | tabloid | 507 | 2.0% |
| thisisanfield.com | tabloid | 7 | — |

**reliable 19,235 (76.3%) · tabloid 5,965 (23.7%) · 15 rows with NULL tier.**

> **2026-09-13 non-journalism cleanup.** 1,068 template pages (TV/streaming listings,
> match-preview templates, player hub pages, FPL tips, quizzes) were marked
> `body_valid=0, invalid_reason='non_journalism'` — marked, never deleted. Rules live in
> `validity.non_journalism_titles` in the config; `corpus extract --revalidate` re-applies
> them to existing bodies in seconds without re-extracting. FourFourTwo shrank hardest
> (1,510 → 770): it was the main source of streaming guides. Guardian's share *rose*
> (40.2% → 41.7%) because the junk had been padding the denominator — the same
> wrong-denominator error class as the earlier readiness-gate bug.

**The tabloid tier is a feature, not noise.** Conflicting coverage of transfer sagas is
what makes golden-set questions about *synthesis*, *temporal resolution*, and *source
reliability* possible. Do not "clean away" tabloid speculation. (Do filter auto-generated
stat/archive stubs — those are not journalism and they poison dedup.)

### Databases
- **`data/corpus.db`** (188 MB) — the corpus. Tables:
  - `articles` — `id, title, url, domain, published_at, language, body_full, body_valid,
    invalid_reason, content_hash, dup_of, extractor_version, fetched_at, tier`
  - `chunks` — schema exists, **currently EMPTY (0 rows)**; populated in Phase 3.
  - `ingest_log` — 280 completed `(query, window)` pairs; enables window-level resume.
- **`data/cache/html.sqlite`** (1.9 GB) — 33,589 rows of gzipped raw HTML. The corpus DB is
  unaffected if it is deleted, but keep it: re-extraction reads from here, so improving the
  extraction rules costs seconds instead of re-downloading everything.
- **`*.db-wal` / `*.db-shm`** — SQLite's Write-Ahead Log and its shared-memory index,
  created automatically because `db.py` sets `PRAGMA journal_mode = WAL` (chosen so an
  interrupted scraper leaves no half-written state). **These are NOT junk.** A non-empty
  `-wal` holds committed transactions not yet folded into the main file — deleting it loses
  data. Never delete one that is non-zero, or while any process has the DB open. They
  normally vanish when the last connection closes cleanly. Git-ignored.
- *(Removed 2026-09-04: `data/corpus.sqlite`, a stray 0-byte file, deleted by the user.)*

### Known data facts
- **~4,653 rows have NULL `body_full`** — this is normal, not a bug: 4,622 are ESPN pages
  that return HTTP 202 with an empty body (JS/bot wall) plus ~31 permanent errors. **None
  are in the usable set.**
- Coverage is even month-to-month; no temporal holes (holes would break time-filtered
  retrieval and are indistinguishable from quiet news weeks).

## 6. The code we have (`src/newsrag/corpus/` — 2,584 lines, Phase 1, complete)

All of it is reachable through the `corpus` console script (`pyproject.toml`
`[project.scripts]`) and mirrored as `make` targets.

| File | Lines | Purpose |
|---|---:|---|
| `db.py` | 151 | Schema, connections (WAL + foreign keys), idempotent column migrations. Two DBs by design: clean corpus vs. bulky disposable HTML cache. |
| `guardian.py` | 234 | **Guardian API ingest** — returns full plain-text bodies directly, so no fetch/extract needed. The reliable-tier anchor. |
| `cc.py` | 500 | **Common Crawl** discovery (CDX index) + WARC byte-range fetch. The tabloid tier — CC bypasses the Cloudflare walls that block direct scraping. |
| `mediacloud_ingest.py` | 223 | **Media Cloud** discovery for BBC/Sky/ESPN (they block crawlers, so they need a separate discovery path). Per-source monthly cap to prevent one domain dominating. |
| `gdelt.py` | 367 | GDELT ingest — **BENCHED, do not use** (see §8). Kept for reference: retry/backoff, circuit breaker, ingest ledger. |
| `fetch.py` | 150 | Async HTML fetch: `robots.txt` honoured per domain, per-domain concurrency cap + global cap, gzipped cache, resume. |
| `extract.py` | 178 | HTML → clean body via `trafilatura` + `ftfy`; validity filter with reasons; `strip_boilerplate` removes per-source chrome. |
| `dedup.py` | 160 | MinHash (128 perms) + LSH + union-find clustering; deterministic canonical election (valid → longest → earliest → lowest id); marks `dup_of`. |
| `stats.py` | 201 | Corpus health funnel, loss accounting, domain concentration, temporal coverage, and the **Phase-2 readiness gates**. |
| `smell.py` | 171 | Per-document anomaly battery — catches contamination that aggregate gates miss. |
| `audit.py` | ~215 | **Discovery only.** Local LLM (Ollama) labels a sample ARTICLE/TEMPLATE to surface junk classes the lexical rules miss. Never writes to the corpus. Measured too imprecise to decide deletions — see its docstring for the numbers and why. |
| `annotate.py` | ~140 | Blind terminal annotation (`a`/`t`/`s`/`q`), incremental + resumable. Built to validate `audit.py`; it is the prototype for the Phase-2 golden-set annotator. |
| `cli.py` | 243 | The `corpus` command: `init, ingest, ingest-guardian, ingest-mediacloud, ingest-cc, fetch-cc, fetch, extract, dedup, stats, smell`. |

**Quality verification ladder** (use it, in order): L1 aggregate gates (`stats`) → L2
per-document smell tests (`smell`) → L3 random-sample human read → L4 LLM-labelled audit →
L5 task-based. **Rule: an LLM may LABEL for auditing, but must NEVER rewrite body text.
Cleaning is always deterministic (regex/filters).**

## 7. The code still to be built

Both packages exist but are **empty except for `__init__.py`**.

### `src/newsrag/rag/` — Phases 3–4
| Planned module | Purpose |
|---|---|
| `chunk.py` | Split bodies into chunks; `fixed` vs `semantic` strategies (an ablation axis). Populates the empty `chunks` table with char offsets for exact-passage citation. |
| `embed.py` | sentence-transformers embeddings, cached locally. |
| `index.py` | Vector store (FAISS/Chroma) + BM25 index. |
| `retrieve.py` | Dense, BM25, and hybrid fusion — the headline ablation. |
| `rerank.py` | Cross-encoder reranking over the top-N candidates. |
| `generate.py` | Anthropic SDK: grounded, cited answers that may abstain. |

### `src/newsrag/eval/` — Phase 5 (the thesis)
| Planned module | Purpose |
|---|---|
| `golden.py` | Load/validate the golden set. |
| `retrieval_metrics.py` | recall@k, MRR, nDCG. |
| `generation_metrics.py` | Groundedness, citation accuracy, abstention. |
| `judge.py` | LLM judge **plus its validation against human annotation** — an unvalidated judge is not evidence. |
| `baseline.py` | Closed-book (no retrieval) contamination floor; report *lift* over it, not raw accuracy. |
| `run.py` / `ablations.py` | One-command runs producing the README results table + cost/latency. |

### `data/golden/` — Phase 2, the next thing to build
The **one data artifact committed to git** (our own questions, not article text). Target
~150 questions, each with a reference answer **and annotated relevant document IDs** — that
third field is what makes retrieval measurable at all.

**Design constraint — gradable ≠ structured.** Questions must be **text-native**: *why* a
manager was sacked, what a club's statement said, what fee was reported and by whom,
conflicting reports across a saga. **Not** pure statistics (goals, scores, standings) —
those are a database lookup (SQL), not retrieval, and would make the project look naive.

## 8. Domain and corpus sourcing

**Domain: football/soccer news**, top-5 European leagues + Champions League (+ Eredivisie),
2024-08 → 2026-06. Chosen for **affinity**: the user understands football well enough to
build and judge a good golden set — and golden-set quality is the whole project. It is also
rich in *temporally evolving, conflicting* stories (transfer sagas), the best possible
material for RAG evaluation. Energy was the original domain and was dropped precisely
because he had no affinity for it.

The domain is a **configuration knob** (`config/football_queries.yaml`), so the same
machinery retargets to F1 or cybersecurity without touching the core.

### Source strategy — three sources, three different access problems
| Source | Role | Why this one |
|---|---|---|
| **Guardian Open Platform API** | reliable anchor | Returns full bodies directly — no scraping, no extraction. |
| **Common Crawl** | tabloid tier | CC has tabloid HTML that Cloudflare blocks us from fetching directly. |
| **Media Cloud** | reliable-tier discovery | BBC/Sky are directly fetchable but block crawlers, so discovery needs a separate path. |

### ⛔ GDELT is dropped — do not resurrect it without discussion
The DOC 2.0 API put the user's IP into a persistent 429 penalty state; every retry
re-armed it. `gdelt.py` remains only as reference. **ESPN is also a dead end** for simple
fetch: 4,622 soccer URLs discovered, 0 usable (HTTP 202 with empty body — a JS/bot wall).
Reviving ESPN would need Wayback or a JS-capable fetcher; deferred.

## 9. Known caveats and accepted exceptions

- **Guardian is 42.0% of the usable corpus and FAILS the "no domain >40%" readiness gate.**
  The user decided on 2026-07-22 to **accept this deliberately** rather than pad the corpus,
  game the metric, or over-collect. `stats` still reports the honest FAIL. **This is a
  documented exception, not falsified data — do not "fix" it by changing the gate.**
  3 of 4 gates PASS.
  - Encoded in `stats.py::ACCEPTED_FAILURES` (2026-09-13), so the final verdict stops
    saying "not ready" about a closed decision while the FAIL itself stays printed.
  - **The acceptance is self-expiring.** Each entry carries a `valid_while` predicate; this
    one holds only while the top domain is still `theguardian.com` *and* its share is
    ≤42%. If a different domain dominates, or Guardian drifts higher, the gate blocks
    again and demands a new decision. Verified against all three scenarios.
  - ⚠️ **Headroom is now 0.025pp** (41.975% vs the 42% ceiling). Removing ~6 more
    non-Guardian rows trips the gate. This is the mechanism working: it forces a fresh
    decision instead of letting concentration drift silently. **Do not widen the ceiling to make it
    fit** — that is the gaming this whole mechanism exists to prevent. Either diversify
    the corpus (more BBC/Sky/tabloid) or ask the user for a fresh, dated decision.
  - It is hardcoded rather than config-driven **on purpose**: accepting a gate should cost
    a reviewable code change, not a knob that is easy to turn when a number is
    inconvenient. Add an entry only after the user explicitly decides; record who and when.
- 15 usable rows have a NULL `tier` (cosmetic; classify if convenient).

## 10. Hard-won operational lessons (do not relearn these)

- **Not all corpus noise is equal — the test is "does it corrupt the measurement?"**
  This is the principle that decides every future cleanup:

  | Kind | Example | Effect | Action |
  |---|---|---|---|
  | **Distractor** | off-scope football (women's, AFCON), tabloid speculation | Competes plausibly and is *not* the answer → **tests precision** | **Keep** |
  | **Inert** | TV listings, "<Player> News" hub pages | Answers nothing and doesn't look like an answer | Leave it |
  | **Corrupting** | **near-duplicates**, bodies that are mostly nav chrome | Breaks the metric directly | **Fix** |

  This retroactively explains which work paid off: dedup was essential (15 copies of the
  right story score as 14 misses — the number lies about the system), template filtering
  was not (inert, and removing it risked deleting real articles). A corpus where every
  document is relevant makes `recall@k` meaningless — you need a haystack.

- **Define the rubric before you measure agreement.** Two imperfect annotators who share
  a rubric still give a useful number: their agreement is the ceiling any classifier can
  meaningfully hit. Two annotators using *different* rubrics give noise. Our 18% "model
  precision" was mostly the latter — the human was judging journalism-ness, extraction
  quality and usefulness at once. This risk is far larger in Phase 2, where "is this
  document *relevant* to this question?" is exactly as fuzzy.

- **Retry-with-backoff is right for a transient blip and actively harmful under a sustained
  throttle** — each retry multiplies the load that caused the throttle. Use a **circuit
  breaker**: stop after N consecutive failures instead of digging deeper. Applied to both
  GDELT and Common Crawl (`DataThrottled`).
- **Silent failure is worse than loud failure.** A swallowed error becomes a hole in
  temporal coverage that is indistinguishable from a quiet news week. Two real bugs of this
  class were found and fixed: a `except ValueError: return []` that discarded a full week of
  articles over one malformed JSON escape, and windows dropped without being reported.
- **Ledgers make interruption free.** `ingest_log` records completed `(query, window)` pairs,
  so a run can be stopped at any time and resumed without re-requesting.
- **Common Crawl's CDX `from`/`to` filter capture time, not publication time** — old pages
  crawled recently slip through. Filter by URL-path date at discovery *and* trim by the real
  publication date after fetch.
- **Auto-generated non-journalism poisons dedup** (stat stubs, "all content" archive index
  pages). Filter them explicitly on every new source.
- **A source-specific filter is NOT a general predicate.** Reusing `cc.py::looks_like_article`
  (which rejects any `?query`) across the whole table deleted ~2,181 real BBC articles whose
  URLs carry tracking parameters. Recovered via ledger clear + re-run.
- **Measure concentration over the right denominator.** The readiness gate originally
  measured domain share over *total ingested*; bodyless/invalid/duplicate rows padded the
  denominator and produced a false PASS. Measured over the *usable* corpus it was an honest
  FAIL. Pick the denominator that matches the decision being made.

## 11. Data and copyright (a real constraint)

- **Never commit third-party article text.** News bodies are the outlets' copyright. The
  repo stores URLs, IDs, embeddings, short snippets, and the *scripts to rebuild* the
  corpus. `data/corpus.db` and the HTML cache are git-ignored.
- The **one committed data artifact** is `data/golden/` — our own questions and annotations.
- Sources are public and licensing-safe. The fetcher honours `robots.txt` and caps
  concurrency per host.
