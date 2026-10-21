"""The `review` command: a human audits candidate questions into the golden set.

Exposed as the `review` console script (see pyproject.toml [project.scripts]).

WHY THIS EXISTS
The golden-drafter subagent writes candidates to `data/candidates/`; nothing reaches
`data/golden/golden_set.jsonl` until a human has read it. This tool is that step: it shows
each pending candidate with its evidence highlighted inside the article, and records one
decision — approve, edit, or reject. That human decision is the annotation; AGENTS.md §7
asks for the approve / edit / reject rates to be reported, and this tool records them.

WHY IT IS NOT `golden review`
1. The drafter may run `uv run golden ...`. If audit lived there, the agent could approve
   its own candidates — the one thing the whole drafting design exists to prevent.
2. `golden` promises one JSON object on stdout, for an agent to parse. An interactive tool
   for a person cannot keep that promise, and should not pretend to.

AND WHY THAT IS NOT LEFT TO INSTRUCTIONS ALONE
`review` refuses to start unless stdin is an interactive terminal. When an agent runs a
shell command, stdin is not a keyboard, so the tool exits before showing anything. It is
the first guard in this pipeline that is technical rather than a line in a prompt.

LAYOUT (built in four parts, 2026-10-21): 1 · data layer — pending candidates, safe
in-place updates, golden ids. 2 · the screen — quotes highlighted in context, the
not-annotated neighbours, club concentration. 3 · the actions — approve, edit, reject,
full text. 4 · the loop, the audit rates, and the `review` command.

Usage::

    uv run review                    # every pending candidate
    uv run review --batch trial-2    # one batch
    uv run review --stats            # rates + concentration only (read-only)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import date, datetime, timezone
from pathlib import Path

from newsrag.eval.golden import DEFAULT_PATH as GOLDEN_PATH

CANDIDATES_DIR = Path("data/candidates")


def _require_tty() -> None:
    """Exit unless a person is at the keyboard. See "AND WHY..." in the module docstring.

    Both ends are checked: stdin, because that is where the decisions come from, and
    stdout, because output piped somewhere else means nobody is reading the article.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        sys.exit("review is interactive and must be run by a person in a terminal — "
                 "it will not run under an agent or a pipe.")


def pending_candidates(batch: str | None = None) -> list[tuple[Path, dict]]:
    """Every candidate still awaiting a decision, oldest file first, in file order.

    Args:
        batch: limit to `data/candidates/<batch>.jsonl`; None means every batch.
    Returns:
        [(file, record), ...] for records whose "status" is "pending".

    A malformed line fails loudly with file:line, as everywhere else in this pipeline: a
    silently skipped line would be a candidate nobody ever audits, and nobody would know.
    """
    paths = [CANDIDATES_DIR / f"{batch}.jsonl"] if batch else sorted(CANDIDATES_DIR.glob("*.jsonl"))
    out: list[tuple[Path, dict]] = []
    for path in paths:
        if not path.exists():
            sys.exit(f"no candidates file at {path}")
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.exit(f"{path}:{n}: {exc}")
            if record.get("status") == "pending":
                out.append((path, record))
    return out


def update_candidate(path: Path, candidate_id: str, updates: dict) -> None:
    """Rewrite one candidate's record in place, safely.

    WHY NOT EDIT THE LINE DIRECTLY: a JSONL file cannot be changed in the middle without
    rewriting it. Writing over the original and dying halfway (Ctrl+C, a crash) would leave
    a truncated file — every candidate after the cut gone. So the new content goes to a
    temporary file next to it, and `os.replace` swaps it in: on the same filesystem that
    swap is atomic — readers see either the old file or the new one, never half of each.

    Raises KeyError if the id is not in the file: updating nothing silently would make a
    decision look recorded when it was not.
    """
    lines = path.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip() and json.loads(line).get("candidate_id") == candidate_id:
            lines[i] = json.dumps({**json.loads(line), **updates}, ensure_ascii=False)
            found = True
            break
    if not found:
        raise KeyError(f"candidate {candidate_id} not found in {path}")

    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, path)


def next_golden_id(existing_ids: list[str]) -> str:
    """The id for the next approved item: one past the highest "qNNNN" in the golden set.

    WHY "highest + 1" AND NOT "count + 1": ids must never be reused. If a golden item is
    ever deleted, count + 1 would hand its id to a new question — and any stored result
    keyed by that id would silently start describing a different question.

    Case-insensitive on purpose: a stray "Q0099" (one appeared in the hand-written file
    once) still counts, so its number can never be handed out again.

    Expected:
        ["q0001", "q0002", "q0005"] -> "q0006"
        ["q0001", "Q0099"]          -> "q0100"
        []                          -> "q0001"
    """
    numbers = [int(m.group(1)) for i in existing_ids
               if (m := re.fullmatch(r"q(\d+)", i, re.IGNORECASE))]
    return f"q{max(numbers, default=0) + 1:04d}"


def _now() -> str:
    """UTC timestamp for audit records — one format everywhere, comparable as text."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Part 2: the screen ----------------------------------------------------------------
#
# WHAT THE AUDITOR NEEDS TO SEE, in the order they need it: the question and its answer
# first (what is being claimed), then each quote inside its paragraph (is the claim
# supported?), then the articles nobody annotated (is the annotation complete?). That last
# section exists because of trial-2: Sky 60511 answered the Tudor question and was left out,
# and the auditor only found it by going looking.

#: Colour is a convenience, never information: everything highlighted is also marked
#: with » « so the screen still works in a terminal without colour, or with NO_COLOR set
#: (the de-facto standard opt-out: https://no-color.org).
_COLOUR = "NO_COLOR" not in os.environ and sys.stdout.isatty()  # no escape codes in a pipe or file


def _style(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def _bold(t: str) -> str: return _style(t, "1")
def _dim(t: str) -> str: return _style(t, "2")
def _mark(t: str) -> str: return _style(f"»{t}«", "1;30;43")   # black on yellow


def _width() -> int:
    """Wrap to the terminal, capped so long lines stay readable on a wide screen."""
    return min(shutil.get_terminal_size((100, 24)).columns, 110) - 2


def _wrap(text: str, indent: str = "  ") -> str:
    return textwrap.fill(text, _width(), initial_indent=indent, subsequent_indent=indent)


def quotes_in_context(body: str, quotes: list[str]) -> tuple[list[str], list[str]]:
    """Each paragraph that holds one or more of `quotes`, shown ONCE with all of them
    highlighted. Returns (snippets in article order, quotes not found).

    WHY `_normalise` FROM cli.py AND NOT A COPY: this must find a quote exactly when
    `submit` would accept it. Two definitions of "the quote occurs" would drift, and the
    screen would one day say "not found" about a quote the checker passed.

    WHY GROUP BY PARAGRAPH: a paragraph is what a reader judges a sentence by, and two
    quotes from one paragraph shown separately would print it twice — noise in exactly
    the part that needs careful reading (seen on the Tudor candidate). A quote that crosses
    a paragraph break (whitespace normalisation lets it pass `submit`) falls back to a
    window of the whole normalised body around it.
    """
    from newsrag.eval.cli import _normalise

    wanted = [_normalise(q) for q in quotes if _normalise(q)]
    placed: set[str] = set()
    snippets: list[str] = []
    for para in body.split("\n"):
        p = _normalise(para)
        here = [q for q in wanted if q in p and q not in placed]
        for q in here:
            p = p.replace(q, _mark(q), 1)
            placed.add(q)
        if here:
            snippets.append(p)

    whole = _normalise(body)
    for q in wanted:
        if q in placed or (at := whole.find(q)) == -1:
            continue
        start, end = max(0, at - 250), min(len(whole), at + len(q) + 250)
        snippets.append("…" + whole[start:at] + _mark(q) + whole[at + len(q):end] + "…")
        placed.add(q)
    return snippets, [q for q in wanted if q not in placed]


def unannotated_neighbours(conn, record: dict, limit: int = 10) -> list[dict]:
    """Articles near the candidate's own (same club, ±4 days) that it does NOT annotate.

    The same notion as the drafter's `related` tool, run from every relevant document and
    merged, closest first. Not a verdict — most will be unrelated news about the same club.
    It is a short list to scan for the one that also answers the question.
    """
    from newsrag.eval.draft import related_docs

    annotated = {d["doc_id"] for d in record["relevant_docs"]}
    seen: dict[int, dict] = {}
    for doc_id in annotated:
        try:
            for d in related_docs(conn, doc_id, days=4, limit=50):
                if d["doc_id"] not in annotated:
                    best = seen.get(d["doc_id"])
                    if best is None or d["days_apart"] < best["days_apart"]:
                        seen[d["doc_id"]] = d
        except ValueError:
            continue  # a doc outside the usable corpus; validate() reports that elsewhere
    return sorted(seen.values(), key=lambda d: (d["days_apart"], d["doc_id"]))[:limit]


def render_candidate(conn, record: dict, position: int, total: int) -> str:
    """The full screen for one candidate, as a string — printed by the loop in part 3.

    Returned rather than printed so it can be tested without a terminal.
    """
    from newsrag.eval.draft import get_docs

    w = _width()
    out = [_bold(f" candidate {position} of {total} · {record['candidate_id']} · "
                 f"{record['type']} ".center(w, "─"))]
    out.append(_bold("QUESTION"))
    out.append(_wrap(record["question"]))
    out.append(_bold("ANSWER"))
    out.append(_wrap(record["reference_answer"] or "(empty)"))
    out.append(f"{_bold('as_of')} {record.get('as_of') or '—'}    "
               f"{_bold('source')} doc {record.get('source_doc_id')}    "
               f"{_bold('drafted_by')} {record.get('drafted_by')}")
    if record.get("warnings"):
        out.append(_bold("WARNINGS (allowed by the drafter — its reason is in the notes)"))
        out += [_wrap(f"! {w_}") for w_ in record["warnings"]]
    if record.get("notes"):
        out.append(_bold("DRAFTER'S NOTES"))
        out.append(_wrap(record["notes"]))

    quotes: dict[int, list[str]] = {}
    for e in record.get("evidence") or []:
        quotes.setdefault(e["doc_id"], []).append(e["quote"])
    levels = {d["doc_id"]: d["level"] for d in record["relevant_docs"]}
    for doc in get_docs(conn, list(levels)):
        out.append("")
        out.append(_bold(f"── doc {doc['doc_id']} · {doc['domain']} · "
                         f"{doc['published_at'][:10]} · level {levels[doc['doc_id']]} "
                         .ljust(w, "─")))
        out.append(_wrap(doc["title"]))
        if quotes.get(doc["doc_id"]):
            snippets, missing = quotes_in_context(doc["body"], quotes[doc["doc_id"]])
            out += [_wrap(s) for s in snippets]
            out += [_wrap(f"! quote NOT FOUND in the article: {q!r}") for q in missing]
        else:
            out.append(_dim(_wrap("(no evidence quote — added by a human edit)")))

    neighbours = unannotated_neighbours(conn, record)
    out.append("")
    out.append(_bold("── not annotated: same club, ±4 days ".ljust(w, "─")))
    if not neighbours:
        out.append(_dim("  (none)"))
    for d in neighbours:
        # The prefix ("  60511  2026-03-29  skysports.com    ") is 39 columns wide.
        out.append(f"  {d['doc_id']:>6}  {d['published_at'][:10]}  {d['domain']:<16} "
                   f"{d['title'][: max(20, w - 39)]}")
    return "\n".join(out)


def concentration(conn, records: list[dict]) -> str:
    """How candidates spread across clubs — the measurement chosen over story exclusion.

    Counts the golden set plus the candidates still PENDING — the set as it stands and as
    it may become — by the clubs named in each source article's headline. Approved
    candidates are not counted again (they are already golden items: the first version
    counted them twice, 11 items for 9) and rejected ones never enter the set. A club
    holding a large share is the signal that sampling is clustering; nothing here blocks
    anything, it only makes the clustering visible.
    """
    from collections import Counter

    from newsrag.eval.draft import _clubs_in, get_docs
    from newsrag.eval.golden import load

    sources = [r.get("source_doc_id") for r in records if r.get("status") == "pending"]
    sources += [it.source_doc_id for it in load(GOLDEN_PATH)]
    sources = [s for s in sources if s is not None]
    titles = {d["doc_id"]: d["title"] for d in get_docs(conn, sorted(set(sources)))}
    clubs = Counter(c for s in sources for c in _clubs_in(titles.get(s)))
    no_club = sum(1 for s in sources if not _clubs_in(titles.get(s)))
    top = ", ".join(f"{club} {n}" for club, n in clubs.most_common(8))
    return (f"{_bold('Concentration')} over {len(sources)} items (golden + pending), "
            f"by club in the source headline: {top or '—'}"
            + (f"; no club named: {no_club}" if no_club else ""))


# --- Part 3: the actions -----------------------------------------------------------------
#
# approve · edit · reject · read. Every decision is written to disk the moment it is made,
# so quitting — even with Ctrl+C — never loses one.

#: The candidate fields a human may change when editing. Exactly the golden schema minus
#: what the tooling owns (id, provenance, dates) — the auditor edits the question, not
#: its paperwork. `evidence` is not here: it stays in the candidates file as the drafter's
#: record, and it is not part of the golden set (it carries article quotes).
EDITABLE = ("type", "question", "reference_answer", "relevant_docs", "source_doc_id",
            "as_of", "notes")


def _append_golden(item) -> None:
    """Append one item to golden_set.jsonl, safe against a missing final newline.

    WHY THE CHECK: the file was edited by hand and, measured 2026-10-21, ends in "}" with
    no newline. Appending blindly would glue the new item onto the last line; `load()`
    would then fail on that line, and the golden set would be broken by its own tool.
    """
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    needs_newline = GOLDEN_PATH.exists() and GOLDEN_PATH.stat().st_size > 0 \
        and not GOLDEN_PATH.read_bytes().endswith(b"\n")
    with GOLDEN_PATH.open("a") as f:
        if needs_newline:
            f.write("\n")
        f.write(json.dumps(dataclasses.asdict(item), ensure_ascii=False) + "\n")


def _confirm_warnings(warnings: list[str]) -> bool:
    """Show validator warnings and ask whether to approve anyway. Default: no."""
    print(_bold("WARNINGS — usually a mistake, occasionally deliberate:"))
    for w in warnings:
        print(_wrap(f"! {w}"))
    return input("approve anyway? [y/N] ").strip().lower() in {"y", "yes", "s", "si", "sí"}


def approve(conn, path: Path, record: dict, fields: dict | None = None,
            confirm=None) -> tuple[bool, str]:
    """Turn a candidate into a golden item. Returns (done, message).

    `fields` is the auditor's edited version (see `edit`); None means approved as drafted.

    WARNINGS BLOCK UNLESS CONFIRMED — the same rule as `golden submit`. The first version
    printed warnings only AFTER approving, so q0007 (a question saying "As of 30 April
    2026" with as_of empty) entered the golden set with the warning shown too late to act
    on (found in the first real audit, 2026-10-21). `confirm` is called with the warnings
    and decides; without it, warnings simply block — the safe default.

    ORDER AND IDEMPOTENCY. Approving writes two files: the golden set, then the candidate's
    status. A crash between the two would leave the candidate "pending" although its item
    is already in the golden set, and the next session would approve it twice. Sources are
    unique by design (`submit` enforces it), so a golden item with the same source means
    "already approved": the candidate is then marked, not appended again.
    """
    from newsrag.eval.golden import GoldenItem, load, validate

    golden = load(GOLDEN_PATH)  # the same file _append_golden writes to — read and write must agree
    drafted = {k: record.get(k) for k in EDITABLE}
    data = drafted | (fields or {})
    # An edit is a CHANGE, not an opened editor: closing it unchanged is an approval as
    # drafted. Found 2026-10-21 — the Tudor item was recorded "edited" with nothing
    # changed, which inflates the edit rate the audit exists to report honestly.
    changed = data != drafted
    clash = next((it for it in golden if data["source_doc_id"] is not None
                  and it.source_doc_id == data["source_doc_id"]), None)
    if clash:
        update_candidate(path, record["candidate_id"], {
            "status": "approved", "golden_id": clash.id, "audited_at": _now(),
            "decision": "already-in-golden"})
        return True, f"already in the golden set as {clash.id} — candidate marked approved"

    item = GoldenItem(
        id=next_golden_id([it.id for it in golden]),
        drafted_by=record.get("drafted_by", "llm"),
        reviewed_by="human",
        edited_by="human" if changed else None,
        created_at=date.today().isoformat(),
        difficulty=record.get("difficulty"),
        **data,
    )
    report = validate([item], conn=conn)
    if report.errors:
        return False, "not approved — fix with [e]dit:\n" + "\n".join(
            f"  ✗ {e}" for e in report.errors)
    if report.warnings and not (confirm and confirm(report.warnings)):
        return False, "not approved — fix with [e]dit, or approve again and confirm:\n" + \
            "\n".join(f"  ! {w}" for w in report.warnings)

    _append_golden(item)
    update_candidate(path, record["candidate_id"], {
        "status": "approved",
        "decision": "edited" if changed else "approved",
        "golden_id": item.id,
        "audited_at": _now(),
        # What the auditor changed, kept beside the drafter's original: this is what the
        # edit rate measures, and what a prompt revision learns from.
        **({"approved_as": {k: data[k] for k in EDITABLE}, "edited_by": "human"}
           if changed else {}),
    })
    note = " (warnings confirmed by the auditor)" if report.warnings else ""
    return True, f"approved as {item.id}{note}"


def edit(conn, path: Path, record: dict) -> tuple[bool, str]:
    """Open the candidate in your editor as JSON; on save, validate and approve it.

    The editor is $VISUAL, else $EDITOR, else nano. For VS Code, set EDITOR="code --wait"
    — without --wait the editor returns at once and nothing is read back.

    No evidence quote is required for a document YOU add: quotes exist to catch a model
    inventing text, and you have just read the article yourself. validate() still runs —
    existence, canonicality, as_of — so a typo'd id is caught.

    An invalid edit is never lost: it reopens with the errors on top, until it validates
    or you clear the file to cancel.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    draft = json.dumps({k: record.get(k) for k in EDITABLE}, ensure_ascii=False, indent=2)
    problems = ""
    while True:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
            tmp.write(problems + draft)
            tmp_path = Path(tmp.name)
        try:
            # shlex, not str.split: an editor path with spaces ("/Applications/Visual
            # Studio Code.app/...") must survive when quoted, as it would in a shell.
            subprocess.run([*shlex.split(editor), str(tmp_path)], check=False)
            text = tmp_path.read_text()
        except FileNotFoundError:
            # e.g. EDITOR="code --wait" before VS Code's `code` command is installed.
            return False, (f"editor not found: {editor!r}. For VS Code, run 'Shell Command: "
                           f"Install code command in PATH' from its command palette, or set "
                           f"EDITOR to the full path of the editor.")
        finally:
            tmp_path.unlink(missing_ok=True)

        body = "\n".join(l for l in text.splitlines() if not l.startswith("//"))
        if not body.strip():
            return False, "edit cancelled (file left empty)"
        try:
            fields = json.loads(body)
            unknown = set(fields) - set(EDITABLE)
            if unknown:
                raise ValueError(f"fields you cannot set here: {sorted(unknown)}")
        except (json.JSONDecodeError, ValueError) as exc:
            draft, problems = body, f"// fix this, or empty the file to cancel:\n// {exc}\n"
            continue

        # Declining a warning reopens the editor with it on top, like an error would.
        done, message = approve(conn, path, record, fields, confirm=_confirm_warnings)
        if done:
            return True, message
        draft = body
        problems = "".join(f"// {line}\n" for line in message.splitlines())


def reject(path: Path, record: dict, reason: str) -> str:
    """Mark the candidate rejected, with the reason.

    The reason is required: rejections are the drafter's feedback. "Statistic, answerable
    from memory" repeated five times is a prompt change; an unexplained rejection teaches
    nothing.
    """
    update_candidate(path, record["candidate_id"], {
        "status": "rejected", "decision": "rejected",
        "reject_reason": reason, "audited_at": _now()})
    return f"rejected: {reason}"


def full_text(conn, doc_ids: list[int]) -> str:
    """Whole articles, for when the paragraph around a quote is not enough to judge.

    Any id is accepted — including a not-annotated neighbour you are considering adding.
    """
    from newsrag.eval.draft import get_docs

    docs = get_docs(conn, doc_ids)
    out = []
    for d in docs:
        out.append(_bold(f"── doc {d['doc_id']} · {d['domain']} · {d['published_at'][:10]} "
                         .ljust(_width(), "─")))
        out.append(_wrap(d["title"]))
        out += [_wrap(p) for p in d["body"].split("\n") if p.strip()]
    missing = sorted(set(doc_ids) - {d["doc_id"] for d in docs})
    out += [f"! doc {i} does not exist" for i in missing]
    return "\n\n".join(out)


# --- Part 4: the loop, the numbers, the command --------------------------------------------

_MENU = "[a]pprove  [e]dit  [r]eject  [f]ull text  [s]kip  [q]uit"


def audit_stats() -> dict:
    """Decisions across every candidate file — the numbers AGENTS.md §7 asks to report.

    "approved" means accepted as drafted; "edited" means accepted after the auditor
    changed it. Both enter the golden set, but they say different things about the
    drafter: a high edit rate is a prompt to revise, a high approve rate on items that
    later prove wrong is a review that was not real.
    """
    from collections import Counter

    counts: Counter = Counter()
    for path in sorted(CANDIDATES_DIR.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                status = record.get("status")
                counts[record.get("decision") if status != "pending" else "pending"] += 1
    counts["approved"] += counts.pop("already-in-golden", 0)
    decided = counts["approved"] + counts["edited"] + counts["rejected"]
    rates = {k: (counts[k] / decided if decided else 0.0)
             for k in ("approved", "edited", "rejected")}
    return {"counts": dict(counts), "decided": decided, "rates": rates}


def _stats_line() -> str:
    s = audit_stats()
    c, r = s["counts"], s["rates"]
    if not s["decided"]:
        return f"{_bold('Audit')}: nothing decided yet · pending {c.get('pending', 0)}"
    return (f"{_bold('Audit')} over {s['decided']} decided: "
            f"approved as drafted {c.get('approved', 0)} ({r['approved']:.0%}) · "
            f"edited {c.get('edited', 0)} ({r['edited']:.0%}) · "
            f"rejected {c.get('rejected', 0)} ({r['rejected']:.0%}) · "
            f"pending {c.get('pending', 0)}")


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def review_loop(batch: str | None = None) -> None:
    """Show each pending candidate and act on one decision per candidate.

    A candidate stays on screen until it is decided or skipped: a failed approval, a
    cancelled edit or a look at the full text all come back to the same candidate rather
    than moving on, so nothing is skipped by accident. Ctrl+C or Ctrl+D quit cleanly —
    every decision was already written the moment it was made.
    """
    _require_tty()
    conn = db_connect()
    queue = pending_candidates(batch)
    if not queue:
        print("Nothing pending." + (f" (batch {batch})" if batch else ""))
        print(_stats_line())
        return

    print(concentration(conn, [r for _, r in queue]))
    print(_stats_line())
    done_now = 0
    try:
        for position, (path, record) in enumerate(queue, 1):
            print("\n" + render_candidate(conn, record, position, len(queue)))
            while True:
                choice = _ask(f"\n{_MENU}\n> ").lower()
                if choice == "a":
                    ok, msg = approve(conn, path, record, confirm=_confirm_warnings)
                    print(msg)
                    if ok:
                        done_now += 1
                        break
                elif choice == "e":
                    ok, msg = edit(conn, path, record)
                    print(msg)
                    if ok:
                        done_now += 1
                        break
                elif choice == "r":
                    reason = _ask("reason (required; empty to go back): ")
                    if reason:
                        print(reject(path, record, reason))
                        done_now += 1
                        break
                elif choice == "f":
                    raw = _ask("doc ids, space-separated (empty = the annotated ones): ")
                    ids = [int(x) for x in raw.split() if x.isdigit()] or \
                          [d["doc_id"] for d in record["relevant_docs"]]
                    print(full_text(conn, ids))
                    print("\n" + render_candidate(conn, record, position, len(queue)))
                elif choice == "s":
                    break
                elif choice == "q":
                    raise KeyboardInterrupt
                else:
                    print(f"unknown choice {choice!r}")
    except (KeyboardInterrupt, EOFError):
        print("\nstopped — every decision so far is saved.")
    print(f"\n{done_now} decided this session.")
    print(_stats_line())


def db_connect():
    """The corpus connection, refusing to invent an empty database (same rule as golden)."""
    from newsrag.corpus import db

    if not db.DEFAULT_DB_PATH.exists():
        sys.exit(f"corpus not found at {db.DEFAULT_DB_PATH} — run review from the project root")
    return db.connect(db.DEFAULT_DB_PATH)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="review",
        description="Audit golden-set candidates: approve, edit or reject each one. "
                    "Interactive — run it yourself in a terminal, from the project root.")
    parser.add_argument("--batch", default=None,
                        help="only this batch (a file name in data/candidates/, without .jsonl)")
    parser.add_argument("--stats", action="store_true",
                        help="print the approve / edit / reject rates and club "
                             "concentration, then exit (read-only, no terminal needed)")
    args = parser.parse_args(argv)
    if args.stats:
        conn = db_connect()
        records = [json.loads(l) for p in sorted(CANDIDATES_DIR.glob("*.jsonl"))
                   for l in p.read_text().splitlines() if l.strip()]
        print(concentration(conn, records))
        print(_stats_line())
        return 0
    review_loop(args.batch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
