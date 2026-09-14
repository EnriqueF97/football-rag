"""Terminal annotation tool — human labels, used to validate the LLM classifier.

Shows one article at a time and records your judgement. Two properties matter:

* **Blind.** The model's label is never displayed. Seeing it would anchor the annotator
  and the agreement number would measure nothing.
* **Incremental.** Every answer is appended to the output file immediately, and already
  annotated ids are skipped on the next run, so you can stop whenever and resume.

Usage::

    corpus annotate --limit 1                       # try it on one article
    corpus annotate --input classified.jsonl --out labels.jsonl
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from newsrag.corpus import db

CHOICES = {
    "a": "ARTICLE",
    "t": "TEMPLATE",
    "s": "SKIP",
}


def _wrap(text: str, width: int = 76, indent: str = "  ") -> str:
    out = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if para:
            out.append(textwrap.fill(para, width=width,
                                     initial_indent=indent, subsequent_indent=indent))
    return "\n".join(out)


def _done_ids(out_path: Path) -> set[int]:
    """Ids already annotated, so a resumed session doesn't ask twice."""
    if not out_path.exists():
        return set()
    done = set()
    for line in out_path.read_text().splitlines():
        if line.strip():
            done.add(json.loads(line)["id"])
    return done


def _load_rows(db_path, input_path: str | None, limit: int, skip_ids: set[int],
               chars: int):
    """Rows to annotate: from a classifier output file, or sampled from the corpus."""
    conn = db.connect(db_path)
    try:
        if input_path:
            ids = [r["id"] for r in json.loads(Path(input_path).read_text())
                   if r["id"] not in skip_ids]
            if not ids:
                return []
            marks = ",".join("?" * len(ids[:limit]))
            rows = conn.execute(
                f"SELECT id, domain, published_at, title, substr(body_full,1,?) "
                f"FROM articles WHERE id IN ({marks})", (chars, *ids[:limit]),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, domain, published_at, title, substr(body_full,1,?) "
                "FROM articles WHERE body_full IS NOT NULL "
                "ORDER BY content_hash LIMIT ?", (chars, limit + len(skip_ids)),
            ).fetchall()
            rows = [r for r in rows if r[0] not in skip_ids][:limit]
    finally:
        conn.close()
    return rows


def annotate(
    db_path=db.DEFAULT_DB_PATH,
    input_path: str | None = None,
    out: str = "data/golden/label_validation.jsonl",
    limit: int = 100,
    chars: int = 1600,
) -> dict:
    """Run the annotation loop. Returns a tally of what you labelled.

    `chars` controls how much of the body is shown. The default is generous on purpose:
    some pages open with an editorial sentence and only then turn into a listing, so a
    short excerpt hides the very thing you are judging. Note this is what *you* see — the
    classifier reads only the first 400 characters, so on a long page your judgement is
    based on strictly more evidence than the model had.
    """
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    already = _done_ids(out_path)
    rows = _load_rows(db_path, input_path, limit, already, chars)

    if not rows:
        print("nothing left to annotate")
        return {}
    if already:
        print(f"resuming — {len(already)} already annotated\n")

    tally: dict[str, int] = {}
    for i, (aid, domain, published, title, body) in enumerate(rows, 1):
        print("\n" + "─" * 78)
        print(f"{i}/{len(rows)}   id {aid} · {domain} · {(published or '?')[:10]}")
        print("─" * 78)
        print("\nTITLE")
        print(_wrap(title or "(no title)"))
        print("\nOPENING")
        print(_wrap(body or "(empty)"))
        print()
        print("  [a] real article     [t] template / not journalism")
        print("  [s] skip (unsure)    [q] save & quit")

        choice = ""
        while choice not in {*CHOICES, "q"}:
            try:
                choice = input("> ").strip().lower()
            except EOFError:
                choice = "q"
        if choice == "q":
            print("\nsaved & stopped.")
            break

        label = CHOICES[choice]
        tally[label] = tally.get(label, 0) + 1
        with out_path.open("a") as f:  # append per item: quitting never loses work
            f.write(json.dumps({"id": aid, "domain": domain, "title": title,
                                "human_label": label}) + "\n")

    print(f"\nannotated this session: {tally}")
    print(f"file: {out_path}  (total {len(_done_ids(out_path))} labels)")
    return tally
