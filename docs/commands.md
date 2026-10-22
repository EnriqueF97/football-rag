# Command Book

> A reference of every command this project uses, grouped by task, each with a plain
> description and an explanation of its flags/values. The goal is **your autonomy**:
> you should be able to run any step yourself without asking what a flag does.
>
> Convention in this file:
> - `<angle brackets>` = a value **you** substitute.
> - `[square brackets]` = optional.
> - Comments after `#` explain the line.
>
> This file grows as the project does. When a phase in
> [`project_progress.md`](../project_progress.md) adds a command, document it here.

---

## 1. Project setup (already done — kept for reference)

```bash
git init                      # create the local git repository (.git/)
uv init                       # scaffold a uv project: pyproject.toml, .python-version, main.py, src/
```

**What `uv init` produced here:**
- `pyproject.toml` — project metadata + the dependency list uv manages.
- `.python-version` — pins the interpreter. Value: `3.13`. uv reads this to pick/build
  the right Python automatically.
- `main.py` — a placeholder entry point (we'll delete or replace it).

---

## 2. Environment & dependencies (`uv`)

> **Rule for this project:** all Python dependencies go through `uv`. Never `pip install`
> into the environment directly — it bypasses the lockfile and breaks reproducibility.

```bash
uv sync                       # create/refresh the virtualenv (.venv/) to match the lockfile
```
Reads `pyproject.toml` + `uv.lock`, builds `.venv/`, installs exactly the locked
versions. This is the command a fresh clone runs first. Idempotent — safe to re-run.

```bash
uv add <package>              # add a runtime dependency and install it
uv add "<package>==<version>" # pin an exact version, e.g. uv add "httpx==0.27.2"
uv add <pkg1> <pkg2> ...      # add several at once
```
Adds the package to `[project.dependencies]` in `pyproject.toml`, resolves, updates
`uv.lock`, and installs into `.venv/`. This *replaces* `pip install`.

```bash
uv add --dev <package>        # add a development-only dependency (tests, linters, etc.)
```
`--dev` puts it in the dev dependency group — installed locally but not required by
someone who only wants to *use* the package.

```bash
uv remove <package>           # remove a dependency (updates pyproject.toml + lock + venv)
uv lock                       # re-resolve and rewrite uv.lock without changing code
uv lock --upgrade             # upgrade locked versions to the newest allowed by pyproject
uv tree                       # print the dependency tree (who pulled in what)
```

```bash
uv run <command>              # run a command inside the project env WITHOUT activating it
uv run python main.py         #   e.g. run a script
uv run corpus stats           #   run the corpus console script
uv run pytest                 #   run tests in the project env
```
`uv run` is the day-to-day way to execute things: it guarantees the command sees the
project's exact dependencies. Prefer it over manually activating `.venv`.

```bash
source .venv/bin/activate     # (optional) activate the venv in your shell
deactivate                    # leave it
```
Only needed if you want a persistent activated shell. `uv run` makes this optional.

**Dependencies this project will add (by phase — install as you reach them):**
| Phase | Command | For |
|-------|---------|-----|
| 1 | `uv add httpx trafilatura ftfy beautifulsoup4 pandas` | fetch + extract corpus |
| 1 | `uv add datasketch` | MinHash near-dup detection |
| 3 | `uv add sentence-transformers faiss-cpu rank-bm25` | embeddings, vector store, BM25 |
| 4 | `uv add anthropic` | generation + LLM judge |
| 5 | `uv add numpy scikit-learn` | metrics (nDCG, agreement) |
| 9 | `uv add langchain langgraph` | stretch agentic layer |

> These are the planned picks, not yet installed. Add them phase by phase so the
> lockfile grows in step with the code, not all at once up front.

---

## 3. Git (portfolio history matters — keep commits small & readable)

```bash
git status                    # what changed, what's staged
git add <path>                # stage a specific file/dir (prefer over `git add .`)
git add -p                    # stage hunk by hunk — good for keeping commits focused
git commit -m "<message>"     # commit staged changes with a message
git log --oneline             # compact history
git diff                      # unstaged changes
git diff --staged             # staged changes (what a commit would capture)
git restore <path>            # discard unstaged changes to a file
git restore --staged <path>   # unstage a file (keep the changes)
```

```bash
git checkout -b <branch>      # create + switch to a new branch
git switch <branch>           # switch to an existing branch
git branch                    # list branches; current is starred
```

> `main` currently has **no commits yet**. The first commit should be the scaffold
> (Phase 0), not a data dump. Before committing, confirm the DB and HTML cache are
> `.gitignore`d — no third-party article text in git (AGENTS.md §3).

---

## 4. Make targets (the project's verb menu)

> These are the one-command steps that rebuild the project. They are **stubs until
> implemented** in the corresponding phase (see `project_progress.md`). Documented here
> so you know the intended interface early.

```bash
make ingest                   # GDELT headlines → SQLite `articles` table (Phase 1.2)
make fetch                    # download article HTML → on-disk cache (Phase 1.3)
make extract                  # cache → full clean bodies via trafilatura (Phase 1.4)
make dedup                    # mark near-duplicate wire copies (Phase 1.5)
make chunk                    # split bodies into chunks (Phase 3.1)
make index                    # embed chunks + build vector/BM25 indexes (Phase 3.2–3.4)
make eval CONFIG=<name>       # run the golden set through one config (Phase 5)
make ablations                # sweep configs → the README results table (Phase 6)
```

**`CONFIG` values** (the retrieval configuration to evaluate):
| Value | Meaning |
|-------|---------|
| `dense` | dense embeddings only (nearest-neighbour) |
| `hybrid` | BM25 + dense fusion |
| `hybrid_rerank` | hybrid + cross-encoder reranker |

Example: `make eval CONFIG=hybrid_rerank` evaluates the full retrieval stack.

---

## 5. CLI (`corpus` console script — wired in Phase 0, logic lands in Phase 1)

> The package exposes a `corpus` command group as a console script (defined in
> `pyproject.toml` under `[project.scripts]`). It works today; the subcommands are
> stubs that fail loudly (`NotImplementedError`) until their Phase 1 logic lands.

```bash
uv run corpus --help                 # list subcommands
uv run corpus <subcommand> [options] # run one (e.g. uv run corpus stats)
```

Equivalent module form, if you prefer: `uv run python -m newsrag.corpus.cli ...`

| Subcommand | Does | Key options |
|------------|------|-------------|
| `init` | create the two SQLite DBs (corpus + HTML cache), schema only — **works today**, idempotent | — |
| `ingest` | fetch GDELT headlines into the DB (metadata only), filtered by the source allowlist — **works today** | `--config <path>` (default `config/football_queries.yaml`) |
| `fetch` | download HTML → cache (async, robots-aware) — **works today** | `--per-domain <N>` (def 3) · `--max-concurrency <N>` (def 15) · `--no-robots` · `--limit <N>` |
| `extract` | cached HTML → full clean bodies — **works today** | `--reextract` (re-run extraction on already-extracted) · `--revalidate` (re-apply only the `validity:` rules to bodies already in the DB — seconds, no re-extraction; use after tuning a rule) · `--config <path>` |
| `dedup` | detect + mark near-duplicates (`dup_of` → canonical) — **works today** | `--threshold <0..1>` (default 0.75) |
| `audit` | LLM-assisted **discovery** of junk classes the lexical rules miss (local Ollama, never writes to the corpus) | `--model` (def `gemma4:e2b`) · `--limit` · `--chars <N>` (body chars the model reads, def 400) · `--scope usable\|invalid` · `--out <json>` |
| `annotate` | blind human labelling in the terminal (`a`/`t`/`s`/`q`), incremental + resumable | `--input <json from audit>` · `--out <jsonl>` · `--limit` · `--chars <N>` (def 1600) |
| `stats` | corpus health report + readiness gate for Phase 2 — **works today**, read-only, no network | — |

**The config file** (`config/football_queries.yaml`) holds three knobs, all editable
without touching code:
- `queries` + `date_range` + `gdelt` — what to search for and how politely (`ingest`).
- `sources` — the domain allowlist; `enabled: false` ingests everything (`ingest`).
- `validity` — body length / keyword / spam rules (`extract`).

> **GDELT rate limits are real:** `sleep_seconds: 12` is the default for a reason. A
> short sleep earns `429 Too Many Requests` and the whole sweep returns nothing.

**Value notes:**
- `--per-domain <N>` — simultaneous requests **per domain** (default 3). Small is
  polite; too high gets you rate-limited or blocked. `--max-concurrency <N>` is the
  global cap across all domains (default 15). `--no-robots` disables robots.txt
  (conscious override); `--limit <N>` fetches at most N articles (for testing).
- `--threshold <0..1>` — MinHash Jaccard similarity above which two articles count as
  near-duplicates (default **0.75**). Higher = stricter (only near-identical copies
  collapse); lower risks merging genuinely different articles. **Careful:** LSH's
  threshold is an S-curve, not a hard cutoff — a pair sitting *at* the threshold is
  ~50/50 to be detected, so set it *below* the similarity you mean to catch. (Our known
  syndicated pair estimates at 0.80 and was missed at 0.80, caught at 0.75.)
- `--start` / `--end` — inclusive date bounds for the GDELT query window (`YYYY-MM-DD`).

**`uv run corpus repair-titles`** — restores titles whose HTML entities Media Cloud mangled
(Sky's `&#x27;` / `&quot;` / `&amp;` stored as `" ;"`, e.g. "Nusken ;s strike"), using the
page's cached HTML. A repair is applied only if re-mangling it reproduces the stored title,
so it never guesses. **Dry run by default** — prints every change and writes nothing.
- `--apply` — write the changes, in one transaction (all or nothing).
- `--use-current-html-title` — opt-in: for usable rows whose headline Sky rewrote after
  discovery, take the page's current title (it matches the stored body, which came from
  that same page). Changes words, not just punctuation — review the dry run first.
- Safe to re-run: already-repaired titles no longer match, so a second run changes 0.
  `corpus smell` flags any title still carrying the artifact.

> These subcommands and flags are the **planned** interface; exact names may shift as
> Phase 1 is implemented. This table will be updated to match the real CLI.

---

## 5b. CLI (`golden` console script — Phase 2 golden-set tools)

The terminal face of `eval/draft.py` and `eval/golden.py`, used by the human **and** by
the `golden-drafter` Claude Code subagent — literally the same tools. Run from the project
root. **Every command prints exactly one JSON object** with `"ok": true/false`; on
`false`, `"errors"` lists what to fix, and the exit code is 1. Bad flags follow the same
contract (argparse errors are rerouted). `golden <cmd> --help` is the per-command manual.

| Command | Does | Key flags |
|---|---|---|
| `uv run golden sample-docs` | single articles spread across months × outlets — **titles + 300-char preview**; for factual / causal / temporal | `--n 5`, `--seed 0` |
| `uv run golden sample-bundles` | groups on the same club + 4-day block from ≥3 outlets — titles only; for multi_doc / contradictory | `--n 3`, `--block-days 4`, `--min-domains 3` |
| `uv run golden related DOC_ID` | same-club articles within ±days, closest first — titles only; follow a story. With `--contains`: trace one fact across a wide window — body must contain every word, club may be in the body, each result has a snippet (lexical search: see AGENTS §6b for its bias) | `--days 4`, `--limit 15` (50 with `--contains`), `--contains WORD` (repeatable; prefer single words to phrases) |
| `uv run golden get ID [ID ...]` | **full bodies** — the only step with enough text to write a question; any missing id fails the call | — |
| `uv run golden submit FILE` (or `-` for stdin) | checks a candidate (schema, `validate()`, evidence shape, grounding, source not reused) and appends it to `data/candidates/<batch>.jsonl` **only if all pass**; reports every error at once. **Warnings block too** (a saved candidate marks its source used, so it could not be fixed afterwards). Includes a **same-batch overlap** warning: a candidate sharing a relevant doc with another in its batch (across batches, sharing is normal) | `--batch NAME` (one per parallel agent) · `--allow-warnings` (save anyway; requires `notes` explaining why) |

Reading is two-step on purpose: sampling shows previews, `get` shows full text — every
printed character stays in the agent's context. Sampling excludes **every document already
used** by the golden set or any candidate (pending, approved or rejected), as source or as
relevant — a diversity rule. `submit` rejects only a reused *source* — a correctness rule;
sharing a relevant article with another question is fine. Give each batch its own starting
`--seed`: sampling is deterministic, so batches starting from the same seed see the same
stories first.

---

## 5c. Audit (`review` console script — humans only)

Audits golden-set candidates into `data/golden/golden_set.jsonl`. **Deliberately not a
`golden` subcommand**: the drafter agent may run `golden`, and must never approve its own
work. It also **refuses to run without an interactive terminal**, so an agent or a pipe
cannot drive it. Run it yourself, from the project root.

```bash
uv run review                     # every pending candidate
uv run review --batch trial-2     # one batch
uv run review --stats             # approve / edit / reject rates + club concentration (read-only)
```

Each candidate shows the question and answer, every evidence quote highlighted inside its
paragraph, and the **articles of the same club ±4 days it does not annotate** — scan these
for one that also answers. Keys:

| Key | Does |
|---|---|
| `a` | approve as drafted → validated, next `qNNNN` id, appended to the golden set |
| `e` | edit the candidate as JSON in `$VISUAL`/`$EDITOR` (default `nano`; for VS Code `EDITOR="code --wait"`), then validate and approve. An invalid edit reopens with the errors on top; empty the file to cancel. No evidence quote is needed for a document you add |
| `r` | reject — a reason is required (it is the drafter's feedback) |
| `f` | full text of the annotated docs, or of any ids you type (e.g. a not-annotated neighbour) |
| `s` / `q` | skip / quit (Ctrl+C and Ctrl+D also quit) |

Every decision is written when made, so quitting never loses one. Safe if interrupted
mid-approval: a candidate whose source is already in the golden set is marked, not
appended twice.

---

## 6. Secrets

```bash
cp .env.example .env          # create your local secrets file (Phase 0.4)
```
`.env` holds real values (e.g. `ANTHROPIC_API_KEY=sk-...`) and is **git-ignored**.
`.env.example` holds the *names only*, committed as a template. Never commit `.env`.

---

## Quick mental model

```
uv  → dependencies & running code   (replaces pip + python)
git → version history               (portfolio artifact — keep it clean)
make→ project verbs                 (rebuild steps, one command each)
CLI → the corpus tooling            (uv run corpus ...)
```
