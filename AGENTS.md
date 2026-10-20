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
- `docs/learning_plan_agents.md` — my learning plan for building agents and programming with
  agents, mapped onto the phases. It does not change the phase order; it marks the places
  where a change would be my decision (e.g. pulling Phase 9 forward).

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

**Overall completion: 25%.** Phase 1 (corpus) finished 2026-07-22.

| # | Phase | Weight | Status |
|---|---|:---:|---|
| 0 | Project scaffolding | 5% | ✅ done |
| 1 | Corpus construction | 15% | ✅ done — 25,215 usable articles |
| 2 | **Golden evaluation set** | 20% | 🟨 **IN PROGRESS — 2.1 design + tooling done; 5 of ~150 questions written (2026-10-17)** |
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

### §6b. `src/newsrag/eval/` — Phase 2 tooling, written

| File | Lines | Purpose |
|---|---:|---|
| `golden.py` | 365 | `GoldenItem` / `ValidationReport` schema, `load()` (JSONL, fails loudly with `file:line`), `validate()` (rubric rules), `_check_docs()` (corpus-backed: existence, canonicality, `body_valid`, **no relevant doc published after `as_of`**). **`as_of` is allowed on any question type** (2026-10-20, after trial-1): `type` says how many articles an answer needs, time-dependence is a separate axis, so the old "as_of on a non-temporal question → wrong type?" warning was removed; instead a question whose text says "as of" while `as_of` is empty warns. "In late November 2024, what did X say?" needs no as_of (what was said never changes); "As of <date>, what was reported…?" does (later articles change what was known). `_is_iso_date()` enforces strict `YYYY-MM-DD` via a parse + round-trip — `date.fromisoformat` alone accepts `"20241124"` and `"2024-W47-7"` on Python ≥3.11. Both date checks added 2026-10-17 after 2 of the first 6 hand-written questions shipped a malformed or wrong-year `as_of` that the validator could not see. Verified against the live DB. |
| `draft.py` | 151 | `sample_docs()` — stratified over **(month, domain)**, 138 buckets, seeded and reproducible, round-robin so Guardian's 42% share does not become the golden set's shape. `used_source_ids()` feeds `exclude=` so repeated sessions hand over new material. Verified: exact counts, exhaustion guard, reproducibility, no duplicates, 138/138 buckets at n=150. **`sample_bundles()` written 2026-10-18** (implemented by Claude at the user's explicit request): groups documents by (club in headline, 4-day block), keeps groups with ≥3 outlets, stratifies across clubs — input for `multi_doc`/`contradictory`. Verified: 1,034 qualifying bundles; first 20 come from 20 different clubs; reproducible per seed; exclude honoured; exhaustion guard. Both sampling queries now `ORDER BY id` — SQL guarantees no row order otherwise, and a seeded shuffle is only reproducible over a fixed input order (no change to today's output, verified). `CLUB_ALIASES`, `_clubs_in()` and `get_docs()` are written and verified. **`related_docs(doc_id, days=4, limit=15)` written 2026-10-18** (by Claude at the user's request): same-club documents within ±days of a seed, closest first (`(days_apart, doc_id)` key, so deterministic), capped — lets the agent follow a thread without a free-text search tool (which would act as a retriever and bias `relevant_docs`). Verified: seeded on the Ten Hag sacking it recovers 59 docs spanning 24/10–01/11, the story a fixed block splits; invalid id raises; club-less headline returns `[]`. |

| `cli.py` | 512 | **The `golden` console script** (2026-10-19, written by Claude at the user's request, part by part): `sample-docs`, `sample-bundles`, `related`, `get`, `submit` — the terminal face of the tools above, so the `golden-drafter` subagent and the human use the same ones. Contract: one JSON object on stdout, `"ok"` + `"errors"`, exit code mirrors ok — argparse errors rerouted to honour it. Reading is two-step (previews, then `get`) to keep the agent's context lean. `submit` is the **only** write path to `data/candidates/`: schema → `validate()` → evidence shape → grounding → source-not-reused, all errors reported in one pass; **warnings block too** (2026-10-20) unless `--allow-warnings` is passed with a non-empty `notes` — a candidate saved with a warning could never be fixed, because saving marks its source article as used; ids and provenance (`drafted_by="llm"`, `reviewed_by=""`) set by code, never by the drafter. **`_check_grounding()`** (written by Claude at the user's request, 2026-10-19) answers only "does this quote exist in the article?" — never "does it support the answer?" or "is the answer true?", which the human judges at audit with the quote highlighted. A quote passes if, after `_normalise` on both sides (curly→straight quotes, en/em dash→hyphen, `…`→`...`, NBSP→space, any whitespace run→one space; case kept), it is a **contiguous** substring of the body and ≥25 chars. Look-alike set chosen by measurement over the usable bodies (curly quotes 0% — ftfy strips them — en dash 42%, `…` 16%, NBSP 5%). Paraphrases and `...`-stitched quotes fail by design; two passages = two evidence entries. Verified on real articles in both directions for each look-alike. Commands documented in `docs/commands.md` §5b. |

**`.claude/agents/golden-drafter.md`** (132 lines, 2026-10-19, tuned 2026-10-20, committed with the repo so
every clone gets the same agent): the drafting subagent. Frontmatter `tools: Bash, Read`,
`model: sonnet` — no Write/Edit, so `golden submit` is its only sanctioned write path (Bash
could still write; the prompt's Boundaries section and `git status` are what contain it).
Sections: why the golden set matters → the `golden` tools → procedure (sample, scan, `get`
the full text, submit by heredoc, fix-or-drop after two failures, rotate `--seed`, stop
after three empty rounds rather than lower the bar) → what makes a good question (reads
`RELEVANCE_RUBRIC.md`, which wins on conflict; five types — **no `unanswerable`**, those
are human-written; six rules, each with its reason and a real bad→fixed example from
the first hand-written items) → evidence rules matching `_check_grounding` exactly →
boundaries → a fixed final-report format including skip reasons. Prompt style: reasons
over rules, no all-caps imperatives. **First run 2026-10-20 (batch `trial-1`, 1 causal +
1 multi_doc):** both candidates passed `golden submit` first try, tree clean afterwards
(it stayed inside `data/candidates/`), 11 sources skipped with reasons. Not yet audited by
the user. Observed: `multi_doc` is the hard type — most bundles are redundant coverage
where one article already answers.

**Review of trial-1 (2026-10-20), verified against the articles.** The causal item (doc
1919) is faithful and keeps the source's hedge, but its question copies the article's
phrasing ("brought to an early close"). The multi_doc item (Bellamy's Wales contract,
BBC 59379 + Sky 60563) has correct facts and complete annotation — the only other doc with
the £700,000 clause, BBC 59598, is dated 1 June, after the question's date — but three
flaws: (1) "As of 30 April 2026" in the text with `as_of` empty, which the agent did *by
following the prompt* (as_of was described as temporal-only); (2) it calls the contract a
"barrier", which Sky never does; (3) it staples two unrelated details rather than
following a story. **The user keeps that candidate in `trial-1.jsonl` undecided.**
Resulting changes, each tied to an observed problem: validator (as_of on any type; "as of"
without as_of warns), `submit` (warnings block unless `--allow-warnings` + notes), and the
prompt — as_of for any time-bound question, what a good `multi_doc` is (a story across
articles, not stapled facts), the "barrier" case under the premise rule, a **new rule 7:
ask in your own words** (copied phrasing favours keyword search, one of the systems under
test), and how to handle warnings. **Next: `trial-2`**, including one `contradictory`
— the agent spotted Guardian reporting Parker's unbeaten run as 33 games where Sky/BBC
say 31.

Who edited docs during trial-1: the *main* session that launched the agent (per the
standing rule), not the subagent, which has no Edit/Write tool — confirmed from the
session transcript. Sessions should say plainly when they update these files.

`src/newsrag/corpus/remote.py` (258 lines) is **scaffolded but unimplemented** — GCS
snapshot push/pull (Track C). Inert until a bucket exists; see §12.

**Quality verification ladder** (use it, in order): L1 aggregate gates (`stats`) → L2
per-document smell tests (`smell`) → L3 random-sample human read → L4 LLM-labelled audit →
L5 task-based. **Rule: an LLM may LABEL for auditing, but must NEVER rewrite body text.
Cleaning is always deterministic (regex/filters).**

## 7. The code still to be built

`rag/` is **empty except for `__init__.py`**. In `eval/`, `golden.py` and `draft.py` are
written (see §6b); the rest is still to come.

> ⚠️ **Binding constraint on everything indexed.** Chunking and embedding must select on
> the usable predicate — `body_valid = 1 AND dup_of IS NULL` (`smell.py::_USABLE`) — never
> over `articles` wholesale. The golden set annotates **canonical documents only**, on the
> premise that near-duplicates are never indexed and so can never be retrieved. Index the
> twins and that premise breaks: every annotation becomes an under-count, silently and
> uniformly across all configurations. `eval/golden.py::_check_docs` enforces the golden-set
> half of this; nothing yet enforces the indexing half.

### `src/newsrag/rag/` — Phases 3–4
| Planned module | Purpose |
|---|---|
| `chunk.py` | Split bodies into chunks; `fixed` vs `semantic` strategies (an ablation axis). Populates the empty `chunks` table with char offsets for exact-passage citation. **Must filter on `_USABLE`** — see the constraint above. |
| `embed.py` | sentence-transformers embeddings, cached locally. |
| `index.py` | Vector store (FAISS/Chroma) + BM25 index. |
| `retrieve.py` | Dense, BM25, and hybrid fusion — the headline ablation. |
| `rerank.py` | Cross-encoder reranking over the top-N candidates. |
| `generate.py` | Anthropic SDK: grounded, cited answers that may abstain. |

### `src/newsrag/eval/` — Phase 5 (the thesis)
| Planned module | Purpose |
|---|---|
| `golden.py` | **Written.** `GoldenItem`/`ValidationReport` schema, `load()` (JSONL, fails loudly with file:line), `validate()` (rubric rules: ids, types, relevance levels, answerable vs unanswerable, `as_of`), `_check_docs()` (corpus-backed: existence, canonicality, `body_valid`). Verified against `data/corpus.db`. |
| `retrieval_metrics.py` | recall@k, MRR, nDCG. |
| `generation_metrics.py` | Groundedness, citation accuracy, abstention. |
| `judge.py` | LLM judge **plus its validation against human annotation** — an unvalidated judge is not evidence. |
| `baseline.py` | Closed-book (no retrieval) contamination floor; report *lift* over it, not raw accuracy. |
| `run.py` / `ablations.py` | One-command runs producing the README results table + cost/latency. |

### `data/golden/` — Phase 2, the next thing to build
The **one data artifact committed to git** (our own questions, not article text). Target
~150 questions, each with a reference answer **and annotated relevant document IDs** — that
third field is what makes retrieval measurable at all.

**Drafting workflow — decided 2026-10-18 (closes "D1").** After writing the first 5 items by
hand, the user decided an LLM drafts and a human approves:

```
sample_docs / sample_bundles  ->  LLM drafts (structured JSON + verbatim evidence quotes)
  ->  data/candidates/*.jsonl  (staging; git-ignored via data/**)
  ->  human review tool: approve / reject / edit, evidence shown in context
  ->  data/golden/golden_set.jsonl  (only approved items; drafted_by="llm", reviewed_by="human")
```

- **Model and harness — revised 2026-10-19:** a Sonnet model running **inside Claude Code
  as a subagent** (`.claude/agents/golden-drafter.md`), **not** a hand-written loop
  against the Anthropic API. The user's call: he wants a model with a ready-made harness,
  not to program one. Consequences: no API key and no `anthropic` dependency; usage comes
  out of his Claude Code plan, so there is no per-run dollar figure to compute; the
  agent's tools are **terminal commands** (a `golden` CLI over `eval/draft.py` and
  `eval/golden.py`) rather than Python functions; and its restriction to those commands
  is by instruction, not enforcement — `git status` is the backstop. Not local Ollama
  (measured weak in `audit.py`), not Haiku (judged too weak for synthesis).
- **The human review is the annotation, not a rubber stamp.** Measure and report the
  approve / edit / reject rates — that number is what proves the review was real.
- **Grounding check:** every draft must quote, per document, a verbatim span supporting
  the reference answer; code verifies the span occurs in that document's body before the
  draft reaches review. `validate()` cannot detect a hallucinated answer; this can.
- **Contamination:** Claude drafts and a same-family model later answers. The closed-book
  baseline (2.4) is now the central measurement of that, and the README must say so.
- **Article bodies leave the machine** — Claude Code sends what the subagent reads to the
  model (unlike `audit.py`, which is local). Public news, but stated.
- **Candidates never go in `data/golden/`** — that folder is committed, and candidates
  carry article quotes.
- **Agent, not pipeline — decided by the user 2026-10-18.** A tool-using agent gets the
  *same tools the user would use* — `sample_docs`, `sample_bundles`, `get_docs`,
  `validate` — chooses and relates articles itself, and writes candidates; the user audits.
  (The recommendation on record had been pipeline-first; the user chose the agent, which
  also serves his agent-building learning plan.) Consequence: the functions in
  `eval/draft.py` are the agent's toolbox, so their docstrings double as tool
  descriptions. Caveat kept on record: any search tool the agent is later given acts as
  a retriever and can bias which documents count as relevant — prefer the bundle tools.

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

- **A validator that encodes two rules can encode two *contradictory* rules, and only data
  reveals it.** `_check_docs` originally had both "a near-duplicate must not be annotated"
  (error) and "a canonical's twins must also be annotated" (warning). Every unit-style
  example had one twin or none, so both passed. Run against a real 10-member cluster
  (BBC republishing one Milner story across ten club pages), no annotation could satisfy
  both: annotating the twins produced nine errors, omitting them produced a warning. The
  resolution came from a convention already in the codebase — `_USABLE` excludes twins
  everywhere, so only canonicals are annotated. **When two rules in one checker disagree,
  the tie-breaker is what the rest of the system already does, not which rule reads better.**
- **Claude Code loads `.claude/agents/` only when a session starts.** A subagent defined
  mid-session is invisible to it ("Agent type '…' not found") until the session restarts.
  Hit on 2026-10-19 with `golden-drafter`. Create agent files at the start of a session,
  or plan a restart before the first run — and do not substitute a general-purpose agent
  told to "follow the file": it has every tool, so it does not test the real boundaries.
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
- **A private cloud bucket is not publication.** Snapshotting the corpus to GCS (Track C)
  is backup, not redistribution — so the bucket stays private, never public-read. The
  git rule is unchanged: bodies never enter the repo.

## 12. Google Cloud Platform (Track C, added 2026-09-21)

A secondary learning goal: working GCP experience for the user's CV. The full plan lives
in `project_progress.md` → **Track C**; this section holds the rules that bind code.

- **A service earns its place by doing real work.** Bolted-on cloud is worse than none —
  a reviewer who knows GCP reads it as cargo cult. Where something is used for learning
  rather than necessity (BigQuery over ~3,000 result rows, for instance), **the README
  says so.** Honesty about that is worth more than the service.
- **SQLite is a local-file database.** The GCS snapshot is *pulled down and queried
  locally*. Do **not** query the `.db` in place over the network, and do **not** reach for
  a GCS FUSE mount: SQLite's locking assumes POSIX semantics that object storage does not
  provide, and a writer over FUSE risks corrupting the file. If remote querying is ever
  genuinely needed, that is a migration to Cloud SQL, not a mount.
- **Secrets via Secret Manager**, never baked into an image or committed. `.env` stays
  git-ignored.
- **Budget alert before the first resource.** Verify pricing directly; never quote it from
  memory.
- ⛔ **Vertex AI Vector Search is excluded** — its index endpoint bills hourly whether
  queried or not, which is the standard surprise bill. FAISS locally, or `pgvector` on
  Cloud SQL if the store must be remote. ⛔ **No GKE or Dataflow** — nothing here needs
  Kubernetes or distributed data processing.
