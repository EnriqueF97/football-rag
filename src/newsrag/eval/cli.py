"""The `golden` command: the golden-set tools, as terminal commands.

Exposed as the `golden` console script (see pyproject.toml [project.scripts]).

WHY THIS EXISTS
The golden set is drafted by a Claude Code subagent (`.claude/agents/golden-drafter.md`)
and audited by a human (AGENTS.md §7). A subagent cannot import Python functions — it
works through a terminal, exactly as a person would. So the functions in `draft.py` and
`golden.py` need a terminal face. That is this module: a thin layer, no logic of its own
beyond deciding what goes in and what comes out. Every real decision lives in the
functions it calls, so the human and the agent use literally the same tools.

THE CONTRACT EVERY SUBCOMMAND FOLLOWS
1. stdout carries exactly one JSON object, and nothing else. The agent parses it; a stray
   print() would break that. Anything meant for a human goes to stderr.
2. Every response has "ok": true or false. On false, "errors" is a list of strings that
   say what to fix — the agent reads them and corrects itself.
3. The exit code mirrors "ok" (0 / 1), so a shell or a script can branch on it without
   parsing anything.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from newsrag.corpus import db

# Candidates are staged here, outside data/golden/: that folder is committed to git, and a
# candidate carries verbatim article quotes (copyright, AGENTS.md §11). data/** is
# git-ignored, so nothing written here can reach the repo by accident.
CANDIDATES_DIR = Path("data/candidates")


def _connect():
    """Open the corpus DB, refusing to invent an empty one.

    WHY THE CHECK: sqlite3.connect() on a path that does not exist silently CREATES an
    empty database. Every query would then fail with "no such table: articles" — an error
    that points at the schema, not at the real cause (wrong working directory, or the
    corpus was never built). Checking first turns a confusing failure into a clear one.
    """
    path = db.DEFAULT_DB_PATH
    if not path.exists():
        _fail([f"corpus not found at {path} — run golden from the project root"])
    return db.connect(path)


def _emit(payload: dict) -> int:
    """Print a successful response and return exit code 0.

    WHY ensure_ascii=False: names like "Ødegaard" or "Gyökeres" stay readable instead of
    becoming "\\u00d8degaard" — which the agent would then have to quote back, verbatim,
    inside evidence. WHY indent=2: the agent and the human both read this output; the
    extra whitespace costs almost nothing next to article bodies.
    """
    print(json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2))
    return 0


def _fail(errors: list[str]) -> None:
    """Print a failure response and exit with code 1.

    WHY sys.exit HERE instead of returning: a failure can be detected deep inside a
    handler (a missing file, an unknown id). Exiting from the spot keeps every handler
    free of "if error: return 1" plumbing, and guarantees nothing else reaches stdout
    after the error — rule 1 of the contract.
    """
    print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
    sys.exit(1)


# --- Read-only commands --------------------------------------------------------------
#
# READING HAPPENS IN TWO STEPS, ON PURPOSE.
# The sampling commands return titles plus a short preview; full bodies come only from
# `get`, for the ids the agent decides it wants. Every character a command prints lands
# in the agent's context and stays there for the rest of its run. Five full articles it
# then discards is ~20,000 characters of context paid for nothing — and a crowded
# context makes the agent worse at the part that matters, not just more expensive. A
# human reads the same way: scan the headlines, open the two that matter.

#: How much of a body the sampling commands show — enough to judge "is there a story
#: here?", not enough to write a question from. Writing needs `get`.
PREVIEW_CHARS = 300


def _used_docs() -> tuple[set[int], set[int]]:
    """Every document already used, across the golden set AND all candidates.

    Returns (sources, all_used): the ids used as a question's source, and the ids used in
    any role — source or relevant. Two sets because they guard two different things:

    - `submit` rejects a candidate whose SOURCE is already a source. That is a correctness
      rule: two questions drafted from one article are correlated and waste audit time.
    - Sampling skips EVERY used document. That is a diversity rule: after trial-1, only
      the source of the Burnley 30-April story was excluded, its other 18 articles kept
      the story alive, and trial-2 drafted from it again. Sharing a relevant article is
      normal, so this never rejects a candidate — it only stops steering back to it.

    WHY CANDIDATES TOO: the golden set only knows audited items; without candidates, a
    second run would re-draft from the first run's documents. Rejected candidates count
    as well: a document that already produced a bad question is a poor bet for the next.

    A malformed candidate line fails loudly with file:line — the same rule as
    `golden.load()`. A silently skipped line would quietly re-open its documents.
    """
    from newsrag.eval.golden import load

    sources: set[int] = set()
    used: set[int] = set()

    def add(source_doc_id, relevant_docs) -> None:
        if source_doc_id is not None:
            sources.add(source_doc_id)
            used.add(source_doc_id)
        used.update(d["doc_id"] for d in relevant_docs)

    for it in load():
        add(it.source_doc_id, it.relevant_docs)
    for path in sorted(CANDIDATES_DIR.glob("*.jsonl")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                _fail([f"{path}:{n}: {exc}"])
            add(record.get("source_doc_id"), record.get("relevant_docs", []))
    return sources, used


def _excluded_ids() -> set[int]:
    """Ids the sampling commands skip: every document already used, in any role."""
    return _used_docs()[1]


def _sample_docs(args: argparse.Namespace) -> int:
    """Single articles, spread across months and outlets -> factual / causal / temporal.

    The body is cut to a preview here (see "two steps" above); `sample_docs` itself still
    returns full bodies, because the Python caller may want them.
    """
    from newsrag.eval.draft import sample_docs

    docs = sample_docs(_connect(), n=args.n, seed=args.seed, exclude=_excluded_ids())
    for d in docs:
        d["preview"] = d.pop("body")[:PREVIEW_CHARS]
    return _emit({"count": len(docs), "docs": docs})


def _sample_bundles(args: argparse.Namespace) -> int:
    """Groups of articles on the same club and days, from several outlets ->
    multi_doc / contradictory. Already titles-only, so passed through unchanged."""
    from newsrag.eval.draft import sample_bundles

    bundles = sample_bundles(
        _connect(), n=args.n, seed=args.seed, block_days=args.block_days,
        min_domains=args.min_domains, exclude=_excluded_ids(),
    )
    return _emit({"count": len(bundles), "bundles": bundles})


def _related(args: argparse.Namespace) -> int:
    """Follow a story from one article: same clubs, nearby days, closest first.

    WHY THE TRY: `related_docs` raises ValueError for an id outside the usable corpus —
    deliberately, so "that id is wrong" and "nothing related" look different. Here that
    exception becomes the contract's error shape instead of a Python traceback, which
    the agent could still read, but would have to dig the message out of.
    """
    from newsrag.eval.draft import related_docs

    try:
        # A content filter is for tracing a fact over a wide window, so its default limit
        # is larger: closest-first with 15 would bury the far reports it exists to find.
        limit = args.limit or (50 if args.contains else 15)
        docs = related_docs(_connect(), args.doc_id, days=args.days, limit=limit,
                            contains=args.contains)
    except ValueError as exc:
        _fail([str(exc)])
    return _emit({"seed": args.doc_id, "count": len(docs), "docs": docs})


def _get(args: argparse.Namespace) -> int:
    """Full bodies for the given ids — the second reading step, and the only place the
    agent sees enough text to write a question and quote evidence.

    WHY FAIL ON A MISSING ID: `get_docs` silently skips ids it cannot find. For a person
    that is harmless; for an agent it is a trap — asked for three, given two, it may
    never notice, and go on to quote from an article it believes it read. So any missing
    id fails the whole call, by name.

    Note what is NOT checked here: whether the article is usable (not a near-duplicate,
    valid body). `submit` enforces that through `validate()`, so it is checked once, in
    the one place that guards the candidates file.
    """
    from newsrag.eval.draft import get_docs

    docs = get_docs(_connect(), args.doc_ids)
    missing = sorted(set(args.doc_ids) - {d["doc_id"] for d in docs})
    if missing:
        _fail([f"doc {i} does not exist in the corpus" for i in missing])
    return _emit({"count": len(docs), "docs": docs})


# --- submit: the only way into the candidates file -----------------------------------
#
# WHAT A CANDIDATE LOOKS LIKE (what the agent sends, as JSON):
#
#   {
#     "type": "causal",
#     "question": "Why did ...?",
#     "reference_answer": "Because ...",
#     "relevant_docs": [{"doc_id": 1194, "level": 2}],
#     "source_doc_id": 1194,
#     "as_of": null,                       # required for "temporal", else omit/null
#     "evidence": [{"doc_id": 1194, "quote": "verbatim sentence from the article"}],
#     "notes": ""                          # optional: anything the auditor should know
#   }
#
# WHAT THE AGENT DOES NOT SEND, AND WHY:
#   - "id": golden ids (q0006, ...) are assigned at audit time, by the review tool. Two
#     agents running in parallel would otherwise both pick "q0006" and collide.
#   - "drafted_by" / "reviewed_by" / "created_at": provenance is recorded by code, never
#     claimed by the drafter. A candidate is drafted_by="llm" and reviewed_by="" (empty =
#     not reviewed yet) — the review tool is what writes "human" there.
#
# "evidence" is the one field the golden set does not have. It exists so a quote can be
# checked against the article mechanically, and so the auditor sees, next to each
# question, the exact sentence that is supposed to answer it.


def _read_candidate(source: str) -> dict:
    """Load the candidate JSON from a file path, or from stdin when source is "-".

    WHY STDIN: it lets the agent submit with a heredoc —
        uv run golden submit - <<'EOF'
        {...}
        EOF
    — without writing a temporary file it would then have to clean up, and without
    escaping quotes inside a shell argument, which is where long JSON strings break.
    """
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail([f"candidate is not valid JSON: {exc}"])
    if not isinstance(data, dict):
        _fail(["candidate must be a JSON object, not a list or a value"])
    return data


#: A quote shorter than this proves almost nothing: "the club said" occurs in hundreds
#: of articles, so finding it in this one says nothing about the answer.
MIN_QUOTE_CHARS = 25

# Characters that look the same to a reader but differ as bytes, mapped to one spelling.
# Share of the 25,215 usable bodies containing each (measured 2026-10-19): curly quotes
# 0% — ftfy straightens them at extraction, but the MODEL may still type them; en dash
# 42%; single-character ellipsis 16%; non-breaking space 5%.
_LOOKALIKES = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',   # curly -> straight quotes
    "–": "-", "—": "-",                       # en / em dash -> hyphen
    "…": "...",                               # one character -> three dots
    "\xa0": " ",                              # non-breaking space -> space
})


def _normalise(text: str) -> str:
    """One spelling for look-alike characters, one space for any run of whitespace.

    Applied to BOTH the quote and the article, so the comparison is symmetric. Case is
    deliberately left alone: changing it is already a step towards paraphrase.
    """
    return " ".join(text.translate(_LOOKALIKES).split())


def _check_grounding(conn, evidence: list[dict]) -> list[str]:
    """Verify every evidence quote actually occurs in its document's body.

    This is the check `validate()` cannot do: validate knows the rules of the golden set,
    but nothing about what an article says. A model that invents a plausible answer will
    usually also invent a plausible quote — and an invented quote is not in the text.

    WHAT IT DECIDES — and what it does not. It answers "does this sentence exist in the
    article?", which is mechanical. Whether the sentence SUPPORTS the reference answer,
    and whether the answer is true, is judged by the human at audit, with the quote
    highlighted in context. This check only guarantees that what the auditor reads is real.

    WHAT COUNTS AS "OCCURS": after `_normalise`, the quote must be a contiguous substring
    of the body — same words, same order. That forgives typography (- vs –, spacing,
    curly quotes) and nothing else: a changed word, or a quote stitched together with
    "...", fails. To lean on two passages, the drafter sends two evidence entries.

    Args:
        conn:     open corpus DB connection.
        evidence: [{"doc_id": int, "quote": str}, ...] — already checked to be well formed.
    Returns:
        One error string per bad quote; [] when every quote is grounded.

    Expected (verified on doc 2409, 2026-10-19):
        quote present verbatim                    -> []
        "-" typed where the article has "–"        -> []   (normalised away)
        one word changed                          -> ["evidence for doc 2409: quote not found in the article: '...'"]
        quote under MIN_QUOTE_CHARS               -> ["evidence for doc 2409: quote is 15 chars, minimum 25"]
    """
    from newsrag.eval.draft import get_docs

    # One query for all the bodies; each body normalised once, however many quotes it has.
    docs = get_docs(conn, sorted({e["doc_id"] for e in evidence}))
    bodies = {d["doc_id"]: _normalise(d["body"]) for d in docs if d["body"]}

    errors: list[str] = []
    for e in evidence:
        doc_id, quote = e["doc_id"], _normalise(e["quote"])

        if doc_id not in bodies:
            # A doc that does not exist is already reported by validate(); one that
            # exists with no body cannot be quoted from, and nothing else says so.
            if any(d["doc_id"] == doc_id for d in docs):
                errors.append(f"evidence for doc {doc_id}: the article has no body to quote from")
            continue

        if len(quote) < MIN_QUOTE_CHARS:
            errors.append(f"evidence for doc {doc_id}: quote is {len(quote)} chars, "
                          f"minimum {MIN_QUOTE_CHARS}")
        elif quote not in bodies[doc_id]:
            shown = quote if len(quote) <= 60 else quote[:60] + "..."
            errors.append(f"evidence for doc {doc_id}: quote not found in the article: "
                          f"{shown!r} — copy it exactly from `golden get {doc_id}`")
    return errors


def _check_evidence_shape(item, evidence) -> list[str]:
    """Structural evidence rules — no corpus access, so they run before anything slow.

    Every relevant document must be backed by at least one quote, and every quote must
    come from a relevant document. The second half matters as much as the first: a quote
    from an article NOT listed in relevant_docs means the answer leans on a document the
    retrieval score will never credit — an annotation gap disguised as evidence.
    """
    if not isinstance(evidence, list) or not all(
        isinstance(e, dict) and isinstance(e.get("doc_id"), int)
        and isinstance(e.get("quote"), str) for e in evidence
    ):
        return ['"evidence" must be a list of {"doc_id": int, "quote": str}']

    quoted = {e["doc_id"] for e in evidence}
    relevant = set(item.doc_ids())
    errors = [f"relevant doc {d} has no evidence quote" for d in sorted(relevant - quoted)]
    errors += [f"evidence quotes doc {d}, which is not in relevant_docs"
               for d in sorted(quoted - relevant)]
    return errors


def _submit(args: argparse.Namespace) -> int:
    """Check a candidate and, only if everything passes, append it to the candidates file.

    ALL CHECKS RUN BEFORE ANYTHING IS REPORTED. Same principle as `validate()`: the agent
    gets every problem in one response and fixes them in one pass, instead of submitting
    five times to discover five errors one at a time.

    The two exceptions stop early, because nothing after them is meaningful: JSON that
    does not parse, and fields that do not fit the schema at all.

    WARNINGS BLOCK TOO, BUT CAN BE OVERRIDDEN. A warning flags something that is usually
    a mistake (a question saying "as of" with no as_of set, found in trial-1). If warnings
    were saved silently, the drafter could never fix them: a saved candidate marks its
    source article as used, so a corrected resubmission would be rejected. So warnings
    stop the submission like errors, and `--allow-warnings` saves anyway — only with a
    non-empty `notes` saying why, so the auditor sees the warning AND the reason.
    """
    from newsrag.eval.golden import GoldenItem, validate

    data = _read_candidate(args.source)
    evidence = data.pop("evidence", None)
    for field in ("id", "drafted_by", "reviewed_by", "created_at"):
        if field in data:
            _fail([f'do not send "{field}" — it is set by the tooling, not the drafter'])

    # A placeholder id: validate() needs one, and the real one is assigned at audit.
    try:
        item = GoldenItem(id="candidate", drafted_by="llm", reviewed_by="",
                          created_at=date.today().isoformat(), **data)
    except TypeError as exc:
        _fail([f"candidate does not fit the golden schema: {exc}"])

    conn = _connect()
    report = validate([item], conn=conn)
    errors = list(report.errors)

    shape_errors = _check_evidence_shape(item, evidence)
    errors += shape_errors
    if not shape_errors:  # grounding needs well-formed evidence to even run
        errors += _check_grounding(conn, evidence)

    # Sources only: sharing a RELEVANT article with another question is normal and must
    # not be rejected — see `_used_docs` for why the two rules differ.
    if item.source_doc_id is not None and item.source_doc_id in _used_docs()[0]:
        errors.append(f"doc {item.source_doc_id} was already used as the source of another "
                      f"question or candidate — pick a different article")

    # SAME-BATCH OVERLAP is a warning, not an error. Across batches, two questions sharing
    # a relevant article is normal. Within one batch it almost always means the drafter
    # stayed on the story it had just used: in trial-3's re-run two of three candidates
    # were about Hilário and shared three documents, because sampling excludes a story only
    # after each submit while `related` excludes nothing. Occasionally deliberate — hence
    # overridable with --allow-warnings and a reason in notes, like every other warning.
    path = CANDIDATES_DIR / f"{args.batch or date.today().isoformat()}.jsonl"
    if path.exists():
        mine = set(item.doc_ids())
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            other = json.loads(line)
            shared = mine & {d["doc_id"] for d in other.get("relevant_docs", [])}
            if shared:
                report.warnings.append(
                    f"shares relevant doc(s) {sorted(shared)} with candidate "
                    f"{other['candidate_id']} in this batch — prefer a different story")

    if report.warnings and not args.allow_warnings:
        errors += [f"warning: {w}" for w in report.warnings]
        errors.append("warnings block a submission too: fix them and resubmit, or — only "
                      "if the choice was deliberate — explain why in notes and resubmit "
                      "with --allow-warnings")
    elif report.warnings and not item.notes.strip():
        errors.append("--allow-warnings needs a reason: say in notes why each warning is "
                      "deliberate")

    if errors:
        _fail(errors)

    # --- Passed: write it ---------------------------------------------------------
    # One file per batch (default: today). Parallel agents each get their own --batch,
    # so no two processes ever append to the same file at once.
    candidate_id = f"c{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"
    record = {
        "candidate_id": candidate_id,
        "status": "pending",              # the review tool moves it to approved/rejected
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **{k: v for k, v in vars(item).items() if k != "id"},
        "evidence": evidence,
        "warnings": report.warnings,      # only via --allow-warnings; the reason is in notes
    }
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return _emit({"candidate_id": candidate_id, "file": str(path),
                  "warnings": report.warnings})


# --- Wiring ----------------------------------------------------------------------------
#
# THE HELP TEXTS ARE THE AGENT'S MANUAL. The subagent can run `golden --help` or
# `golden submit --help` and read them, so they say what each command is FOR and what it
# returns — not only what each flag does.
#
# WHY seed DEFAULTS TO 0 AND IS STILL FINE TO REUSE: the sampled set changes from run to
# run anyway, because every candidate's source joins the exclusion list. Same seed, new
# documents — and any single run stays reproducible for as long as the files don't change.


class _ContractParser(argparse.ArgumentParser):
    """argparse, but its errors honour the contract too.

    WHY: by default a bad flag ("--cantidad 3") makes argparse print plain text to stderr,
    nothing to stdout, and exit with code 2 — breaking all three rules at once, on the
    mistake an agent makes most often. Routing argparse's error through `_fail` gives the
    same JSON shape and exit code 1 as every other failure. Subparsers inherit this class
    automatically, so the fix covers every subcommand.
    """

    def error(self, message: str) -> None:
        _fail([f"{message} — run `golden {self.prog.split(' ', 1)[-1]} --help` for usage"
               if " " in self.prog else f"{message} — run `golden --help` for usage"])


def build_parser() -> argparse.ArgumentParser:
    parser = _ContractParser(
        prog="golden",
        description="Golden-set tools. Every command prints one JSON object with "
                    '"ok": true/false; on false, "errors" says what to fix. '
                    "Run from the project root.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "sample-docs",
        help="single articles spread across months and outlets (titles + preview); "
             "for factual / causal / temporal questions",
    )
    p.add_argument("--n", type=int, default=5, help="how many articles (default: 5)")
    p.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    p.set_defaults(func=_sample_docs)

    p = sub.add_parser(
        "sample-bundles",
        help="groups of articles on the same club and days from several outlets "
             "(titles only); for multi_doc / contradictory questions",
    )
    p.add_argument("--n", type=int, default=3, help="how many groups (default: 3)")
    p.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    p.add_argument("--block-days", type=int, default=4,
                   help="width of each time block in days (default: 4)")
    p.add_argument("--min-domains", type=int, default=3,
                   help="minimum distinct outlets per group (default: 3)")
    p.set_defaults(func=_sample_bundles)

    p = sub.add_parser(
        "related",
        help="articles naming the same club(s) as DOC_ID within +-DAYS, closest first "
             "(titles only); for following a story. With --contains: trace one fact "
             "across a wider window",
    )
    p.add_argument("doc_id", type=int, help="the article to start from")
    p.add_argument("--days", type=int, default=4, help="window half-width (default: 4)")
    p.add_argument("--limit", type=int, default=None,
                   help="maximum results (default: 15, or 50 with --contains)")
    p.add_argument("--contains", action="append", default=None, metavar="WORD",
                   help="only articles whose text contains this word; repeat for more (all "
                        "must appear). The club may then be named in the body, and each "
                        "result shows a snippet. Prefer single words to phrases — "
                        "'unbeaten run' misses '33 games unbeaten'")
    p.set_defaults(func=_related)

    p = sub.add_parser(
        "get",
        help="full text of the given articles — read these before writing a question "
             "or quoting evidence",
    )
    p.add_argument("doc_ids", type=int, nargs="+", help="one or more article ids")
    p.set_defaults(func=_get)

    p = sub.add_parser(
        "submit",
        help="check a candidate question and, only if every check passes, save it for "
             "human audit; on failure nothing is saved and the errors say what to fix",
    )
    p.add_argument("source",
                   help='candidate JSON file, or "-" to read it from stdin (heredoc)')
    p.add_argument("--allow-warnings", action="store_true",
                   help="save despite validator warnings — only when the flagged choice is "
                        "deliberate, and only with notes explaining why")
    p.add_argument("--batch", default=None,
                   help="candidates file name, without .jsonl (default: today's date); "
                        "give each parallel agent its own")
    p.set_defaults(func=_submit)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
