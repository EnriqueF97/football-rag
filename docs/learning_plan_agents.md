# Learning plan — building agents and programming with agents

Personal learning plan, written 2026-10-14. It rides on top of the build plan in
[`project_progress.md`](../project_progress.md): every step below lands as real work in this
repo, so what I learn shows up in the code (and later on my CV). This file does **not** change
the phase order; where a change could help, it is marked as **a decision for me**.

Two skills, one project:

- **Part A — Building agents:** LLM systems that act. They plan steps, call tools, keep state,
  and loop until a task is done.
- **Part B — Programming with agents:** building software by directing a coding agent (Claude
  Code): clear context, small tasks, tests as the safety net, every diff reviewed. The
  [`WORKING_AGREEMENT.md`](../WORKING_AGREEMENT.md) loop (agent scaffolds and reviews, I write
  the logic) is already a version of this.

My edge in both: **evaluation**. Few people can show whether an agent actually works.

---

## Key concepts (short)

| Concept | What it means |
|---|---|
| **Tool use / function calling** | The model answers with a request to call a tool; my code runs it and sends back the result. That loop *is* an agent. |
| **Workflow vs agent** | Workflow: my code fixes the steps (chain, route, run in parallel, one model grades another). Agent: the model picks the next step. Start with workflows. |
| **State** | What the agent carries between steps (question, retrieved docs, draft answer). |
| **Context engineering** | Choosing what goes into the model's context at each step, and what stays out. |
| **MCP** | Model Context Protocol: a standard way to expose tools and data to any agent (Claude Code included). |
| **Guardrails** | Limits on what the agent may do, and when a human must approve. Includes **prompt injection**: text the agent reads is data, never instructions. |
| **Agent evaluation** | Did it finish the task? Were its steps sensible? Cost and latency per task. An LLM judge counts only after it is validated against human labels. |
| **Observability** | A trace of every step (inputs, tool calls, outputs, tokens, time), so failures can be read, not guessed. |

---

## The plan

Checkboxes are mine to tick. "Done when" says how I know a step is finished.

### Step 0 — Foundations (now, alongside Phase 2)

- [ ] Read Anthropic's article **"Building effective agents"** (workflows vs agents, the
      common patterns, when *not* to build an agent).
- [ ] Read the **tool use** guide in the Anthropic API docs.
- [ ] Write a **throwaway agent loop by hand**, no framework: two tools, `search_corpus(query)`
      (plain SQL `LIKE` on the corpus is enough for now) and `read_article(doc_id)`, and a
      loop that runs until the model stops asking for tools.
      Keep it outside `src/newsrag/` (it is a learning exercise, not product code).
- **Done when:** the loop answers one question by calling both tools, and I can explain every
  message that went back and forth.

### Step 1 — Tests first (Part B; before Phase 3 code)

- [ ] Add **pytest** as a dev dependency with `uv` (say what it buys: fast, cheap checks that
      let an agent verify its own changes).
- [ ] First tests on code that already exists, with clear inputs and outputs:
      `is_valid_body` in `corpus/extract.py`, the near-duplicate logic in `corpus/dedup.py`,
      and `validate()` in `eval/golden.py` (each rule it enforces should have a failing case).
- [ ] From here on, every new function in Phases 3–5 gets at least one test.
- **Done when:** `uv run pytest` passes and covers those three modules.

### Step 2 — Retrieval core (Phase 3, as planned)

- [ ] Build chunking, embeddings, the dense index, BM25 and hybrid retrieval (the Phase 3
      tasks), working in the Part B loop: small tasks, tests, every diff read before commit.
- [ ] Learn on the way: what an embedding is, why BM25 and dense search fail on different
      questions, and why hybrid usually wins.
- **Done when:** Phase 3 is ticked in `project_progress.md`, with tests.

### Step 3 — Generation with tool use in mind (Phase 4)

- [ ] Generation and citations through the Anthropic SDK (Phase 4).
- [ ] Notice where the Step 0 ideas show up: structured outputs, citations as a contract,
      cost per call (Batches API for eval runs).
- **Done when:** Phase 4 is ticked.

### Step 4 — Evaluation harness, including the judge (Phase 5)

- [ ] Retrieval metrics (recall@k, MRR, nDCG) and generation metrics (Phase 5).
- [ ] **Judge validation (5.3):** human-label a subset, measure judge-vs-human agreement,
      report it in the README. This is the same skill an agent evaluation needs.
- **Done when:** Phase 5 is ticked and the README shows the judge agreement number.

### Step 5 — The agent (Phase 9, LangGraph)

- [ ] Build the **retrieve-or-abstain agent** in **LangGraph**: state, nodes (`retrieve`,
      `generate`, `abstain`), a conditional edge for the decision.
- [ ] **Evaluate it with the golden set:** does it abstain on the unanswerable ~10%, and
      only there? Compare with the plain pipeline from Phases 3–4.
- [ ] Add tracing for each step (tokens, latency, which branch was taken).
- **Decision for me:** Phase 9 is "stretch, optional" in the build plan, but LangGraph is on
  my CV. Option: pull it forward to right after Step 4, before Phases 6–8. Tradeoff: the
  ablations and write-up land later.
- **Done when:** the agent runs on the golden set and its abstention results are in the README.

### Step 6 — MCP server and CI (extensions, not in the build plan yet)

- [ ] **MCP server** that exposes the hybrid search as a tool, and use it from Claude Code
      ("search the football corpus for ...").
- [ ] **GitHub Actions:** run `pytest` on every push. Optional: an automated agent review
      on pull requests.
- **Decision for me:** whether these become official phases in `project_progress.md`.
- **Done when:** Claude Code can call the corpus search through MCP, and CI is green.

---

## Part B practices (apply in every step)

1. **Context:** keep `AGENTS.md` true (the standing rule), and give each session one clear,
   small task.
2. **Small loops:** plan, implement one function, test, read the diff, commit.
3. **Never merge what I have not read.** Tests catch regressions; reading catches wrong ideas.
4. **Automate the boring checks:** hooks or CI for tests and formatting, not memory.
5. **Parallel work, later:** several agents on separate git worktrees, each on its own task.

---

## Resources

- Anthropic: **"Building effective agents"** (article) and the **tool use** docs.
- **Claude Code docs:** hooks, MCP, subagents, skills, headless mode (`claude -p`).
- **LangGraph:** the official docs and the free course **"Introduction to LangGraph"**
  (LangChain Academy).
- **MCP:** <https://modelcontextprotocol.io> (spec, Python SDK, examples).

## CV hook

When a step lands, update the football-rag text in the job tracker's `master-cv/master_cv.tex`
**and** `master-cv/cv_ats.tex` (same content in both), describing only what exists.
