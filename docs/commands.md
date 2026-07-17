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
| `ingest` | fetch GDELT headlines into the DB (metadata only) — **works today** | `--config <path>` (default `config/football_queries.yaml`) |
| `fetch` | download HTML for articles with no body | `--concurrency <N>` (per-domain cap) |
| `extract` | extract bodies from cached HTML | `--reextract` (re-run on already-extracted) |
| `dedup` | detect + mark near-duplicates | `--threshold <0..1>` (MinHash similarity cutoff) |
| `stats` | print corpus health summary | — |

**Value notes:**
- `--concurrency <N>` — how many simultaneous requests **per domain**, not global.
  Small (e.g. 2–4) is polite; too high gets you rate-limited or blocked.
- `--threshold <0..1>` — MinHash Jaccard similarity above which two articles count as
  near-duplicates. Higher = stricter (only near-identical copies collapse). Typical
  ~0.8. Lower risks merging genuinely different articles.
- `--start` / `--end` — inclusive date bounds for the GDELT query window (`YYYY-MM-DD`).

> These subcommands and flags are the **planned** interface; exact names may shift as
> Phase 1 is implemented. This table will be updated to match the real CLI.

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
