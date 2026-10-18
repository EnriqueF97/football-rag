# Project Progress — Evaluation-First RAG over Football News

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

## Overall completion: **25%**

| # | Phase | Weight | Local % | Status |
|---|-------|:------:|:-------:|--------|
| 0 | Project scaffolding | 5% | 100% | ✅ done |
| 1 | Corpus construction (fix + harden the scraper) | 15% | 100% | ✅ done — 26.4k usable, audited & cleaned; 3/4 gates PASS, Guardian 40.2% **accepted** (deliberate) |
| 2 | Golden evaluation set | 20% | ~25% | 🔨 in progress — design (2.1) complete |
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

## Phase 0 — Project scaffolding · weight 5% · **100%** ✅

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

## Phase 1 — Corpus construction · weight 15% · **100%** ✅ *(code complete; Guardian corpus is REAL — 10.3k full bodies. Source diversity pending: see 1.8)*

> **SOURCE STRATEGY PIVOT (2026-07-19).** GDELT DOC 2.0 is **benched**: diagnosis showed
> a persistent IP-level penalty state (429 on the first cold request; malformed requests
> return 200, so the block gates search execution only; a 5-min fully-quiet gap did not
> clear it; every attempt — including retries and browser tests — re-arms it). Decision:
> do not depend on a de-ban. Replacement architecture, empirically verified via
> Common Crawl CDX + robots.txt checks:
>
> | Tier | Source | Discovery | Bodies |
> |------|--------|-----------|--------|
> | Reliable anchor | **Guardian Open Platform API** (free key in `.env`) | API search | API `body` HTML → cache (done, 1.7) |
> | Tabloid/rumour | **Common Crawl** (mirror, 90min, teamtalk, caughtoffside, fourfourtwo, thisisanfield… — all verified present with status-200 HTML; CC bypasses their Cloudflare 403s) | CC index | WARC byte-range (planned, 1.8) |
> | Other reliable | BBC / Sky / ESPN — **absent from CC** (they block CCBot `Disallow: /`, as does the Guardian) | RSS / Media Cloud / BigQuery GKG if stats show gaps | direct fetch (works: BBC/Independent return 200) |
>
> Contradictory tabloid coverage is a *feature* (golden-set fuel: saga synthesis,
> temporal resolution, source-reliability questions) — never "clean it away".
> BigQuery (`gdelt-bq.gdeltv2.gkg_partitioned`, sandbox free tier) stays on the bench
> as a discovery fallback.

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
- [x] **1.2.8** **Circuit breaker — stop digging.** Comparing against the legacy notebook
      (`notebooks/legacy/03`) showed our request shape is *identical* — same endpoint,
      params, User-Agent, weekly windows. The difference was the error path: the legacy
      skipped a failed window and moved on; ours retried 3×, so a throttled window cost
      **4 requests instead of 1**, amplifying the very load that caused the throttle.
      Lesson: retry-with-backoff is right for a transient blip and actively harmful under
      a *sustained* throttle. Now: 1 retry (not 3), and a `Throttled` breaker aborts the
      run after 3 consecutive window failures. Verified: 6 requests before stopping where
      the old design made 32. The ledger makes stopping free — the next run resumes.
- [x] **1.2.7** **Ingest ledger — window-level resume.** GDELT rate-limits at the IP
      level; a real run hit 429 on its *first* request and no in-run backoff could clear
      it. The ingest was all-or-nothing, so every restart re-requested windows we already
      had and tripped the limiter again. Now an `ingest_log(query, window_start,
      window_end)` table records each completed window, articles are inserted and logged
      **per window** (not per query), and a re-run skips what's done. Order is
      insert-then-log so the worst case is one redundant re-fetch, never lost articles.
      Verified: second run made **0 HTTP calls** and skipped all 4 windows. This turns
      "one uninterrupted 5-hour run" into "run in bursts whenever, progress accumulates."
- [x] **1.2.6** **Malformed-JSON repair.** GDELT DOC 2.0 sometimes emits invalid JSON —
      a captured real case was an unescaped backslash inside an Arabic headline
      (`الدوري 2024 \ 2025`). The original code caught `ValueError` and returned `[]`,
      **silently discarding ~50 articles for that week**. Now `parse_articles()` repairs
      invalid escapes + control characters (verified: legitimate `\"` escapes survive),
      and a genuinely unparseable payload *raises* so the window is reported as a gap
      rather than mistaken for a quiet news week. Same principle as 1.2.5, one layer down.
- [x] **1.2.5** **429 backoff + visible gaps.** A real run hit GDELT throttling within
      minutes. `search_gdelt` now retries 429/5xx with exponential backoff (honouring
      `Retry-After`), and `download_range` *reports* any week-window it still loses
      instead of swallowing it — a dropped window is a **week of missing articles**, and
      a silent gap is indistinguishable from a quiet news week, which would corrupt
      temporal coverage. Re-running retries the gaps (inserts are idempotent).
      Default `sleep_seconds` raised 12 → 20. Verified with mocked 429s.
- [x] **1.2.4** **Source allowlist** (`sources:` in the same config): only reputable
      football-journalism domains enter the corpus. GDELT indexes everything — the first
      smoke run returned a TV guide and two Nigerian aggregators syndicating one story.
      Suffix + www-insensitive matching (`bbc.co.uk` admits `sport.bbc.co.uk`, but
      `notbbc.co.uk` is correctly rejected). Rejections are tallied per domain and
      printed, so we keep visibility into what is excluded. *Deliberately not narrowed
      to one source — multiple outlets covering the same saga IS the conflicting-sources
      material the evaluation needs.*

### 1.3 Fetch layer (`fetch.py`) — *the unblocker* — ✅ *done*
- [x] **1.3.1** `httpx` async fetch with a **per-domain** semaphore (default 3) + a
      global cap (default 15). Courtesy is per host, not aggregate.
- [x] **1.3.2** HTML cached gzip-compressed in `data/cache/html.sqlite` — never hit a
      server twice for the same URL.
- [x] **1.3.3** `robots.txt` honoured per domain (cached, lenient on failure) + browser
      UA. Outcomes labelled by HTTP status; only 2xx + permanent 4xx are cached, so
      transient (429/5xx) errors retry next run. *(Deferred nicety: explicit in-run
      backoff/delay on 429 — currently relies on retry-next-run.)*
- [x] **1.3.4** Resume-capable: skips already-cached URLs. Verified on 8 rows
      (5 ok · 2 blocked 403 · 1 transient 500 that correctly retries).

### 1.4 Extraction (`extract.py`) — *the correctness fix* — ✅ *done*
- [x] **1.4.1** `trafilatura` full-body extract replaces `soup.find_all('p')[:3]`.
      Only HTTP 200 is extracted — a cached 403 block page is not an article.
- [x] **1.4.2** `ftfy.fix_text()` replaces the latin-1↔utf-8 mojibake hack.
- [x] **1.4.3** `is_valid_body` returns *(valid, reason)* so failures are tallied by
      cause (`too_short` / `spam_phrase` / `off_topic`). Rules live in the
      `validity:` section of `config/football_queries.yaml` — a knob, not hardcoded.
- [x] **1.4.4** Runs off the HTML cache, so `--reextract` costs seconds and never
      re-downloads. `EXTRACTOR_VERSION` is stamped per row → extractor quality is an
      ablation knob. Verified: 5/5 cached 200s extracted and valid (780–7,748 chars).

### 1.5 Near-duplicate detection (`dedup.py`) — *NEW, evaluation-critical* — ✅ *done*
- [x] **1.5.1** 5-word shingles + MinHash (128 perms) over bodies (`datasketch`).
- [x] **1.5.2** LSH for candidate pairs (O(n) not O(n²)), then **connected components**
      via union-find to turn pairs into clusters.
- [x] **1.5.3** Canonical elected deterministically — valid → longest → earliest →
      lowest id — and the rest marked `dup_of`. Nothing is deleted; re-running with a
      new `--threshold` recomputes from scratch.
- [x] **1.5.4** Reports cluster count, largest cluster, and canonical count; warns when
      the largest cluster ≥20 (a sign of transitive chaining / too-low threshold).
- **Empirical finding:** LSH's threshold is an S-curve, not a hard cutoff. Our known
  syndicated pair (same Salah story on `dailypost.ng` / `nigerianeye.com`, different
  `content_hash`) estimates at Jaccard **0.80** and was **missed at threshold 0.80**,
  caught at 0.75/0.70/0.60. Default lowered to **0.75**. Canonical rule verified: the
  870-char copy won over the 780-char one. *Tuned on 5 articles — re-validate on the
  full corpus.*

### 1.6 Corpus stats & sanity (`stats.py`) — ✅ *done*
- [x] **1.6.1** **Pipeline funnel** with counts + % at every stage (ingested → cached →
      extracted → valid → canonical), plus a `LOSSES` block that names *where* articles
      were lost. A single "N articles" number hides all of it.
- [x] **1.6.2** Coverage by month (inline histogram) — gaps break time-filtered retrieval.
- [x] **1.6.3** Domain distribution with a >40% over-representation warning — this is
      what validates the source allowlist on real data.
- [x] **1.6.4** Also: fetch outcomes by HTTP status (block rate), duplicate-cluster count
      and largest size (validates the dedup threshold), and body-length distribution.
- [x] **1.6.5** **Readiness gate** — a PASS/FAIL checklist answering "is this corpus good
      enough to build the golden set on?" A golden set annotated over a broken corpus is
      wasted work, and it's the most expensive artifact in the project.
- [x] **1.6.6** Persisted `invalid_reason` (new column + idempotent migration in
      `db.py::_ensure_column`) so losses can be explained after the run, not just during it.

### 1.7 Guardian ingest (`guardian.py`) — ✅ *done, corpus is real*
- [x] **1.7.1** Resumable ingest over the Open Platform API (`corpus ingest-guardian` /
      `make ingest-guardian`; key loaded from `.env` via `uv run --env-file`). Reuses the
      `ingest_log` ledger with 28-day windows — same resume contract as GDELT.
- [x] **1.7.2** **Bodies ride the existing pipeline.** The API's `body` HTML is stored in
      the `raw_html` cache (status 200), so `extract.py` processes Guardian through the
      same trafilatura+ftfy+validity path as any fetched source — same `content_hash`
      semantics for dedup, paragraphs preserved. `fetch` auto-skips (already cached).
- [x] **1.7.3** Transport-error retry (TLS handshake timeout killed the first run; now
      `httpx.TransportError` shares the 429/5xx retry budget).
- [x] **1.7.4** **Backfill executed 2026-07-19**: 10,682 articles, Aug 2024 → Jun 2026,
      ~130 calls, zero failed windows. `extract`: 10,288 valid bodies (99% ok; 64
      off_topic; median 4,649 chars). Monthly coverage even (~400–650/month, no gaps).
- **Readiness gate after this run: 3/4 PASS.** Volume, body length, dup clusters all
  pass; **FAIL: theguardian.com is ~97% of the corpus** — the single-source
  concentration the gate exists to catch. Unblocked by 1.8, not by more Guardian.

### 1.8 Common Crawl ingest — tabloid tier (`cc.py`) — ✅ *executed 2026-07-21*
- [x] **1.8.1** Discovery via the CDX index (`corpus ingest-cc` / `make ingest-cc`):
      8 football URL patterns × monthly crawls, ledger-resumable, patient retries.
      **The CDX index is operationally flaky** (502/504 under load, then went fully
      down mid-run) — the run banked 92 of ~184 pattern×crawl pairs before the outage;
      the rest resume when the index recovers. Discovered ~16k captures.
- [x] **1.8.2** Bodies via WARC **byte-range** from `data.commoncrawl.org` (`fetch-cc`)
      → `raw_html` cache, status 200 — never touches the publisher, no Cloudflare/
      cookies. **CloudFront IP-blocks a burst**: concurrency 8 earned a 403 wall after
      ~2k/16k. Fixed: gentle defaults (concurrency 3 + 0.3s pace), 403 treated as
      retryable-here, and a `DataThrottled` breaker aborts if a batch is majority-403.
      Second harvest at the gentle rate: 13,745/13,747 bodies, no block.
- [x] **1.8.4** `tier` column on `articles` (`reliable`/`tabloid`) — Guardian=reliable,
      CC=tabloid. Migration in `db.py::_ensure_column`. Feeds source-reliability Qs.
- [x] **1.8.6** **Strict-window + junk-page cleanup (the real lesson).** CC's CDX
      `from`/`to` filters *capture* time, not publication time, so recently-crawled OLD
      pages slipped through: 9,873 pre-window articles (FourFourTwo archives back to
      1997; caughtoffside 2006–2015 — verified real via URL-path + `<meta>` dates, not a
      bug). Also two classes of auto-generated non-journalism: FourFourTwo "All content
      Archive | \<month\>" index pages and TEAMtalk per-player stat stubs
      (`/player/{injury,results,fixtures,career,squad,…}`) — these formed the big dup
      clusters. Per user decision (2026-07-21): **strict window** (drop everything
      pre-2024-08). Hardened `cc.py`: archive/stat-stub filters in `looks_like_article`
      + a URL-path window filter at discovery so this never re-enters. Trimmed the DB
      (26,659 → 15,247 rows) and re-ran dedup.
- [ ] **1.8.3** Wayback Machine CDX fallback for URLs CC missed — *deferred* (not needed
      yet; revisit if completing discovery leaves gaps).
- [x] **1.8.5** Re-ran `stats`. **Now 3/4 PASS** (volume, body length, dup cluster<20).
      Temporal coverage clean and even, 2024-08→2026-06, no gaps. **Still FAIL: domain
      concentration — theguardian.com = 70.1%** of 14,590 usable. The honest number: the
      tabloid tier only yielded ~4k in-window usable (discovery half-done + much of CC's
      capture was old). Guardian dominance is now the sole blocker → **1.9**.

### 1.9 Reliable-tier diversification (BBC / Sky / ESPN) — *NEXT, the sole Phase-2 blocker*
> User decision (2026-07-21): close the source-balance gap by broadening the *reliable*
> tier, not (only) by finishing tabloid discovery. To drop Guardian under 40% of the
> in-window corpus we need ~11–12k more non-Guardian in-window articles; BBC/Sky/ESPN
> are high-volume enough to supply that.
- [x] **1.9.1** **Media Cloud ingest — EXECUTED 2026-07-21** (`mediacloud_ingest.py`,
      `make ingest-mediacloud`, key in `.env`). Resolves domain→source id, paginates the
      Online News Archive per (source × month), INSERT OR IGNORE (tier='reliable'),
      ledger-resumable. Discovered BBC/Sky/ESPN. **Three real-world lessons, all now in
      the code:** (a) **per-source cap** `max_per_month` (=500) — BBC yields ~1500/month
      and unbounded would become the *new* >40% domain (swapping Guardian's problem);
      trimmed BBC 30k→10k to the cap. (b) **retry widened** to `requests` connection
      resets *and* Media Cloud `APIResponseError` 5xx (a Cloudflare 521 killed run #2).
      (c) **multi-sport contamination** — "football" is ambiguous (US = NFL: ESPN
      returned NFL/college-football; BBC/Sky returned tennis/rugby). Gate on the soccer
      path segment `/football/` (UK) or `/soccer/` (US) in `_is_soccer_url` + a language
      filter (BBC Hausa etc.). Clean reliable tier: Guardian 10,614 · BBC 6,312 · ESPN
      (see below) · Sky 829 usable.
- [x] **1.9.2** Bodies via **direct fetch** — BBC/Sky fetch fine (200): 13,887/13,909 ok.
      **ESPN is a dead end for simple fetch: it returns `202` with an EMPTY body**
      (JS/bot-mitigation wall). Its 4,622 discovered soccer URLs cache as empty 202s and
      never extract → **0 usable from ESPN**. Recovering them would need a JS-capable
      fetch or Wayback (deferred). Fixed a latent extract quirk this exposed: extract
      only reads `status_code=200`, correctly rejecting the empty 202s.
- [x] **1.6.7** **Readiness-gate denominator bug FIXED (`stats.py`).** The domain-
      concentration gate measured `domain_total / total_ingested`, so bodyless/invalid/
      duplicate rows (ESPN's 4,622 empty 202s especially) padded the denominator and gave
      a **false PASS**. Now measured over the **usable** corpus
      (`domain_usable / total_usable`). This flipped a fake PASS into the honest number:
      **Guardian = 48.8% of usable → real FAIL.** A false PASS is worse than a true FAIL;
      the golden set inherits whatever the corpus actually is.
- [x] **1.9.3** **Finished CC tabloid discovery 2026-07-21** — CDX recovered; ran the 92
      remaining pattern×crawl pairs → 11,661 new articles, 11,661/11,661 bodies fetched
      (gentle rate, no CloudFront block), 10,133 new valid bodies.
- [x] **1.9.4** **ALL 4 READINESS GATES PASS (honest, usable-based).** Final corpus:
      **31,819 usable articles**, 2024-08→2026-06, even monthly coverage. Domain mix (of
      usable): Guardian 33.4% · BBC 19.8% · football365 12.9% · teamtalk 12.6% · 90min
      9.2% · fourfourtwo 8.7% · Sky 2.6% · mirror 0.8%. Reliable + tabloid tiers, no
      source over 40%. `stats` prints "✅ corpus looks ready — proceed to Phase 2".
      (ESPN's 4,622 discovered URLs remain bodyless — the 202 wall — a documented,
      optional future gain via Wayback; not needed, the gate passes without them.)

### 1.10 Quality audit + cleanup (`smell.py`) — ✅ *done 2026-07-22*
*The readiness gates pass on aggregates but can't see inside a document. This step is the
per-document audit that caught what the gates missed.*
- [x] **1.10.1** **Smell-test battery** (`corpus smell` / `make smell`): high-precision
      anomaly checks over the usable set (foreign language, paywall stubs, nav-breadcrumb/
      boilerplate leakage, name-like titles, exact-dup hashes dedup missed, out-of-window
      dates, stub/oversized bodies). Reports counts + examples; never deletes. Runs
      alongside `stats`. This is the Level-2 rung of the verification ladder (Level 1 =
      gates, 3 = random-sample read, 4 = LLM-labelled audit, 5 = task-based).
- [x] **1.10.2** **Contamination it surfaced, then removed** (deterministic filters in
      `cc.py`/`mediacloud_ingest.py` + trims): Spanish edition `90min.com/es/` (891),
      `/authors/` pages, TEAMtalk player hubs (bare `/slug`), BBC live "stats &
      head-to-head" template pages, football365/90min club-hub & index pages, and
      **5,952 out-of-window articles** (CC slug-only URLs bypass the discovery window
      filter; their real HTML-meta date was pre-2024-08). Also fixed the `/author/` vs
      `/authors/` singular/plural gap.
- [x] **1.10.3** **Boilerplate stripped in the body** (`extract.strip_boilerplate`,
      applied in-place + re-hashed + re-deduped): TEAMtalk nav breadcrumb
      "`<club>`/`<comp>` • `<country>`" (3.7k), and **BBC "- Published" chrome** — the
      duplicated leading headline + the timestamp label, plus the related-article teaser
      lines BBC embeds ("`<headline>` - Published`<date>`"). ~13k bodies cleaned across
      passes; BBC "- Published" now 0 in the usable set.
- [x] **1.10.4** **Self-audit caught a real over-delete**: reusing `cc.py`'s
      `looks_like_article` (which rejects any `?query`) on the *whole* table wrongly
      deleted ~2,181 real BBC articles (their Media Cloud URLs carry `?at_medium` tracking
      params). Recovered by clearing the Media Cloud ledger and re-running discovery+fetch.
      Lesson recorded: a filter written for one source is not a general predicate.

> **DECISION (user, 2026-07-22): accept Guardian at 40.2% of the usable corpus.** The
> strict-window cleanup (correctly) shrank the tabloid tier and nudged Guardian just over
> the 40% gate. Rather than pad the denominator (the very bug fixed in 1.6.7), game the
> threshold, or spend more on tabloid/ESPN volume for 0.2%, the user chose to proceed.
> `stats.py` keeps reporting the honest 40.2% FAIL — the gate is not falsified; this is a
> documented, deliberate exception. Phase 1 is **complete**.

### 1.11 LLM-assisted audit (`audit.py`, `annotate.py`) — ✅ *done, negative result*
- [x] **1.11.1** `corpus audit` — local LLM (Ollama / `gemma4:e2b`) labels a sample
      ARTICLE/TEMPLATE with JSON-schema-constrained output. **Never writes to the corpus.**
- [x] **1.11.2** `corpus annotate` — blind terminal annotation, incremental + resumable.
      Prototype for the Phase-2 golden-set annotator.
- [x] **1.11.3** **Worked as a discovery tool**: surfaced three junk classes nobody had
      enumerated (affiliate/sponsored posts, section hub pages, prize-draw terms), each
      then encoded as a deterministic rule. Corpus 26,378 → **25,215 usable**.
- [x] **1.11.4** **Rejected as a filter.** Validated against 72 blind human labels
      (`data/audit/label_validation.jsonl`): best case 4/22 agreement on its flags — it
      would delete ~18 real articles to remove 4 template pages. Input-length ablation
      (400 / 800 / 1600 chars) did not help; more text made it *more* permissive, because
      hub pages are built from article fragments.
- [x] **1.11.5** **Decision (2026-09-14, Enrique): stop here.** Remaining junk is 2-4% and
      mostly *inert* — see the noise taxonomy in AGENTS §10. *Caveat on record: the rubric
      mixed three criteria (journalism-ness / extraction quality / usefulness), so part of
      that disagreement is measurement artefact, not model error.*

**Done when:** `make ingest-guardian && make ingest-cc && make fetch-cc && make extract
&& make dedup` (plus 1.9 reliable-tier ingest + 1.10 cleanups) rebuilds the corpus, every
valid article has a full clean body, near-dups are marked, `stats` + `smell` are clean.
*(2026-07-22: **DONE** — 26,378 usable, 2024-08→2026-06 even coverage, per-document
audited; Guardian 40.2% accepted. Reliable 73% / tabloid 27%. ESPN's 4,622 stay bodyless
(202 wall), excluded from usable — optional Wayback recovery deferred to post-Phase-2.)*

---

## Phase 2 — Golden evaluation set · weight 20% · **~25%** (2.1 design complete)

*Goal: ~150 questions with reference answers **and annotated relevant document IDs**.
The boring part nobody does; the entire thesis. Build it early — it constrains every
downstream choice.*

### 2.1 Design
- [x] **2.1.1** Question taxonomy agreed (6 types, with target mix):
      **factual simple** + **causal/explanatory** ~40% (the base) · **temporal** +
      **contradictory-sources** ~30% (what makes this corpus distinctive) ·
      **multi-document** ~20% · **unanswerable** ~10% (measures abstention).
      Verified the corpus supports them: 13,996 Premier League articles over 23 months
      (median 591/month), and **838 day-club combinations covered by ≥3 outlets** — the
      raw material for multi-doc and contradictory questions. Need ~45 of those; have 838.
- [x] **2.1.2** **Relevance rubric written** → `data/golden/RELEVANCE_RUBRIC.md`, shipped
      with the dataset (labels are uninterpretable without the rule that produced them).
      Graded 0/1/2 rather than binary, because nDCG uses graded relevance and because
      multi-doc questions have no single document holding the whole answer.
      Key content: one operational test per level; an explicit "what you are NOT judging"
      list (quality, extraction, usefulness, topic) — the three criteria that collapsed
      the 1.11 annotation task; boundary cases decided in advance (preview-vs-result,
      as_of dates, near-duplicate inheritance); and a self-consistency check whose score
      is the ceiling any system can be measured against.
- [x] **2.1.3** Schema + **validator** in `src/newsrag/eval/golden.py` (first module of
      the eval package). Fields: `id · type · question · reference_answer ·
      relevant_docs[{doc_id, level}] · as_of · difficulty · source_doc_id · drafted_by ·
      reviewed_by · notes · created_at`.
      Decisions: only levels 1–2 stored (absence = 0); annotation is at **document**
      level, not chunk, so the golden set survives chunking-strategy ablations;
      `source_doc_id` is the audit trail proving each question was written *from* the
      corpus; `drafted_by` records LLM assistance honestly instead of hiding it.
      `validate()` enforces the rubric mechanically — verified against `data/corpus.db`
      that it catches a missing `as_of`, an illegal relevance level, a duplicate question
      id, a doc id absent from the corpus, an unusable body, and **a non-canonical doc id**
      (the silent `recall@k` killer: only `body_valid = 1 AND dup_of IS NULL` is ever
      indexed, so an annotated twin is a hit that can never happen).

### 2.2 Draft (LLM-assisted, human-owned)
- [x] **2.2.1** Sample candidate docs across time/domains for coverage →
      `eval/draft.py::sample_docs` — stratified over (month, domain), 138 buckets, seeded,
      round-robin; `used_source_ids()` excludes documents already written from.
- [ ] **2.2.2** Draft Q/A pairs — **in progress: 5 written (2026-10-17)**, by hand from
      sampled docs; 2 carry `drafted_by: "llm"` where the wording was Claude's (honest
      provenance). Mix so far: temporal 3 · factual 1 · causal 1 — temporal is already
      over target; multi_doc and contradictory still at 0.
      **D1 decided 2026-10-18:** a Sonnet-class Claude model drafts via the API; the human
      approves / rejects / edits every item in a review tool (workflow in AGENTS §7).
  - [x] **2.2.2a** `sample_bundles()` — same-story groups for multi_doc / contradictory.
        Written by Claude at the user's request (2026-10-18); 4-day blocks, ≥3 outlets,
        stratified by club; 1,034 qualifying bundles
  - [x] **2.2.2a'** `related_docs(doc_id)` — agent tool to explore around a seed article:
        same club(s), ±4 days, closest first, capped at 15. Written by Claude at the
        user's request (2026-10-18)
  - [ ] **2.2.2b** Drafting call: structured output + verbatim evidence quote per doc,
        candidates to `data/candidates/` (git-ignored)
  - [ ] **2.2.2c** Deterministic grounding check — every quote must occur in its doc body
  - [ ] **2.2.2d** Review tool: candidate + evidence in context, approve/reject/edit,
        append approved items to `golden_set.jsonl`, log the approve/edit/reject rates
  - [x] **2.2.2e** Pipeline vs agent → **agent** (user's decision, 2026-10-18): it uses
        the user's own tools (`sample_docs`, `sample_bundles`, `get_docs`, `validate`) and
        writes candidates, never `golden_set.jsonl` directly
- [ ] **2.2.3** **Human review every item** — this is the annotation, not a rubber stamp.
      `validate()` now also enforces strict ISO `as_of` and "no relevant doc published
      after `as_of`" — both added after real errors in the first 6 questions.
- [ ] **2.2.4** Deliberately bias toward memory-proof questions (recent, numeric,
      specific) so the set measures *retrieval*, not the model's weights

### 2.3 Relevance annotation — **pooled, in two rounds** (decided 2026-09-21)
*Finding every document that answers a question **is** retrieval, so a complete annotation
cannot be produced before Phase 3 exists. Standard IR practice (TREC) is **pooling**: run
several retrievers, union their top-k, annotate the union. We do that rather than pooling
with SQLite FTS5 now — a lexical-only pool surfaces documents that share words with the
question but not meaning, and it would bias the set toward BM25, which is one of the
systems under test.*

- [ ] **2.3.1a** Round 1 (now): annotate `source_doc_id` — the document each question was
      written from — as the level-2 anchor. Every question is answerable by construction.
- [ ] **2.3.1b** Round 2 (after Phase 3): pool the top-k of dense + BM25 + hybrid, annotate
      only the newly surfaced candidates, and record how many level-2 documents round 2
      added. **That delta is a reportable number** — it measures how incomplete a
      single-anchor golden set is, which almost no portfolio project quantifies.
- [ ] **2.3.1c** Re-run `validate()` after each round; `recall@k` from round 1 is
      provisional and must be labelled as such wherever it appears.
- [x] **2.3.2** Near-duplicate policy settled: **annotate the canonical only**. Twins are
      outside `_USABLE` so they are never indexed; annotating one is an unreachable hit.
      Enforced as an error by `golden.py::_check_docs`. Binds Phase 3 — chunking and
      embedding must select on `_USABLE` or the premise breaks (see AGENTS §7).

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
- [ ] **3.4.1** BM25 index (the lexical half — nails player names, clubs, fees, dates,
      exactly where dense embeddings fumble; football is entity-dense, so this should
      show a clear, explainable win)
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

## Track C — Google Cloud Platform · cross-cutting · **0%**

*A secondary learning goal added 2026-09-21: working GCP experience. **Rule: a service
earns its place by doing real work.** Bolted-on cloud is worse than none — a reviewer who
knows GCP can tell, and "we used BigQuery for 3,000 rows" reads as cargo cult. Where a
service is used for learning rather than necessity, the README says so.*

**Before creating any resource: set a project budget alert.** The trial credit runs out
quietly. Verify current pricing directly — never from memory.

### C.1 Cloud Storage — corpus durability *(do first; solves a problem we have today)*
- [ ] **C.1.1** Bucket + service account + least-privilege IAM role
- [ ] **C.1.2** `corpus push` / `corpus pull` — versioned snapshots of `corpus.db` (188 MB)
      and the HTML cache (1.8 GB), both git-ignored and currently existing on **one laptop
      only**. Common Crawl indexes rotate and Media Cloud results drift, so parts of the
      corpus are not re-scrapeable: this is real data loss exposure, not a hypothetical.
- [ ] **C.1.3** Lifecycle rule on old snapshots; make the README's "rebuild from scratch"
      claim actually true
- **Note:** SQLite is a *local-file* database. The snapshot is pulled down and queried
      locally — we do **not** query it in place over the network (see AGENTS §12).

### C.2 Cloud Run Jobs — parallel ablation grid *(Phase 5; the strongest piece)*
- [ ] **C.2.1** Containerise the eval harness; push to Artifact Registry
- [ ] **C.2.2** API keys via Secret Manager, never baked into the image
- [ ] **C.2.3** Run the config × question grid as a task array; **record serial vs parallel
      wall-clock** — the point is a measured number, not the deployment

### C.3 Cloud Run service — serve the RAG endpoint *(Phase 6)*
- [ ] **C.3.1** Containerised query API, autoscaling to zero
- [ ] **C.3.2** **Cold-start latency measured and reported** — it feeds Phase 7 directly

### C.4 Optional, honestly labelled
- [ ] **C.4.1** Cloud Scheduler + Cloud Run Job: nightly Guardian pull (cheap, real)
- [ ] **C.4.2** BigQuery + Looker Studio over eval results — **not justified by data
      volume** (~3,000 rows). A learning exercise; the README must say so.
- [ ] **C.4.3** Evaluate serving Phase 4 generation through Vertex AI (availability and
      region support to be confirmed, not assumed)

### ⛔ Deliberately excluded
- **Vertex AI Vector Search** — the index endpoint bills **hourly whether queried or
  not**. FAISS locally, or `pgvector` on Cloud SQL if the store must be remote.
- **GKE / Dataflow** — nothing here needs Kubernetes or distributed data processing.

---

## Working agreement

- **Part by part.** Each subtask is reviewable on its own. No "run the whole notebook
  and trust it." When a component lands, its purpose is stated before moving on.
- **`uv` for all Python dependency management.** No bare `pip install`.
- **Small, readable commits.** The git history is part of the portfolio (AGENTS.md §7).
- **Evaluation beats features.** Every tradeoff resolves toward rigor (AGENTS.md §2).
- **No third-party article text in git.** URLs, IDs, embeddings, snippets, and the
  scripts to rebuild — never the bodies (AGENTS.md §3).
