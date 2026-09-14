"""LLM-assisted corpus audit — level L4 of the verification ladder.

Finds non-journalism the lexical filters miss. A local LLM (via Ollama) classifies a
sample of articles as ARTICLE or TEMPLATE; the ones it flags become **candidates for
human review**, and confirmed classes become new deterministic rules in
`validity.non_journalism_titles`.

**This module never writes to the corpus.** That is the whole design:

    LLM discovers  →  human decides  →  deterministic rule applies

It earns its place as a *discovery* tool: it surfaced three junk classes nobody had
enumerated — affiliate/sponsored posts, section hub pages ("<Club> Coverage"), and
prize-draw terms — each of which then became a cheap deterministic rule.

It is NOT good enough to decide what gets deleted. Measured 2026-09-13 against 72 blind
human labels (`data/audit/label_validation.jsonl`):

    body chars   flagged TEMPLATE/1200   agreed with human   false positives
    400                    22                  4 / 22              18
    800                     5                  1 / 4                3
    1600                    7                  1 / 6                5

At best it would delete ~18 real articles to remove 4 template pages — a bad trade when
the whole junk class is 2-4% of the corpus and mostly inert (a "<Player> News" hub page
answers no question, so it rarely competes for a retrieval slot anyway).

Two caveats on those numbers, both honest:

* The rubric was underspecified. The human was implicitly judging three things at once —
  "is it journalism", "did extraction work", "is it useful to me" — while the model
  judged only the first. Part of the disagreement is a measurement artefact, not model
  error. If this is ever retried, define the rubric surgically first.
* Longer input did not help. Doubling and quadrupling the body only made the model more
  permissive; hub pages are *built from* article fragments, so more text makes them look
  more like journalism, not less.

Inference stays local: article bodies never leave the machine.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from newsrag.corpus import db

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma4:e2b"

#: Constrained decoding: the server turns this schema into a grammar and can only emit
#: one of these two labels. Without it the model invents its own vocabulary — an early
#: run returned "NEWS" and "BLOGGING", neither of which was ever offered.
SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": ["ARTICLE", "TEMPLATE"]}},
    "required": ["label"],
}

#: Few-shot: two examples per class, so the ratio doesn't bias the classifier toward
#: either label. The fourth example is deliberately a long-form feature — it was added
#: after the model misfiled exactly that kind of piece as TEMPLATE. Examples that fix an
#: observed failure are worth far more than obvious ones.
PROMPT = """You classify football web pages as ARTICLE or TEMPLATE.

ARTICLE  = a journalist wrote it: reporting, analysis, interview, match report, opinion,
           match preview, prediction column, pundit selection, rumour round-up.
           Previews and predictions ARE articles even when they also list TV channels.
TEMPLATE = nobody wrote it as a story: TV/streaming listing, section or player hub page
           ("<Name> News"), affiliate or sponsored promotion, prize-draw terms, quiz.

Examples:

Title: How to watch Chelsea vs Arsenal: Live streams, TV channels
Opening: Everything you need to know to watch the match, including kick-off times.
Answer: TEMPLATE

Title: Marcus Rashford News
Opening: All the latest Marcus Rashford news, transfer rumours and match coverage.
Answer: TEMPLATE

Title: Brentford v Leeds predictions, expected line-ups, how to watch and stats
Opening: Leeds arrive at the Gtech in poor form and we expect Farke to shuffle his
midfield. Our prediction: a narrow home win, with Wissa to score first.
Answer: ARTICLE

Title: Troy Deeney's Team of the Week: Palmer, Gvardiol, Delap, Tonali
Opening: Every week our pundit picks the eleven who impressed him most, and explains why.
Answer: ARTICLE

Title: Arteta rues missed chances as Arsenal slip to defeat at Anfield
Opening: Mikel Arteta said his side "paid the price" for wastefulness after a 2-0 loss.
Answer: ARTICLE

Title: The end of an era: how one owner reshaped a club over three decades
Opening: When she arrived the club was in the third tier. Three decades on, the story ends.
Answer: ARTICLE

Now classify:

Title: {title}
Opening: {body}
Answer:"""


def classify(title: str, body: str, model: str = DEFAULT_MODEL, timeout: int = 180,
             chars: int = 400) -> str:
    """Label one page. Returns 'ARTICLE', 'TEMPLATE', or 'ERROR:<type>'.

    temperature=0 makes the call deterministic, which is what lets one prompt be
    compared against another without sampling noise muddying the difference.
    """
    payload = {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "options": {"num_ctx": 2048, "temperature": 0},
        "prompt": PROMPT.format(title=title or "", body=(body or "")[:chars]),
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(json.load(resp)["response"])["label"]
    except Exception as exc:  # noqa: BLE001 — recorded as an outcome, never swallowed
        return f"ERROR:{type(exc).__name__}"


def _select(conn, scope: str, reason: str | None, title_like: str | None, limit: int):
    """Pick the rows to audit. Ordered by content_hash: pseudo-random but deterministic,
    so a run can be repeated exactly (RANDOM() would make the experiment unrepeatable)."""
    where = ["body_full IS NOT NULL"]
    params: list = []
    if scope == "usable":
        where.append("body_valid = 1 AND dup_of IS NULL")
    elif scope == "invalid":
        where.append("body_valid = 0")
        if reason:
            where.append("invalid_reason = ?")
            params.append(reason)
    if title_like:
        where.append("lower(title) LIKE ?")
        params.append(title_like.lower())
    sql = (
        "SELECT id, domain, title, url, substr(body_full,1,2000) FROM articles "
        f"WHERE {' AND '.join(where)} ORDER BY content_hash LIMIT ?"
    )
    return conn.execute(sql, (*params, limit)).fetchall()


def audit(
    db_path=db.DEFAULT_DB_PATH,
    model: str = DEFAULT_MODEL,
    limit: int = 300,
    scope: str = "usable",
    reason: str | None = None,
    title_like: str | None = None,
    out: str | Path | None = None,
    chars: int = 400,
) -> dict:
    """Classify a sample and report TEMPLATE candidates. Never mutates the corpus."""
    conn = db.connect(db_path)
    try:
        rows = _select(conn, scope, reason, title_like, limit)
    finally:
        conn.close()
    if not rows:
        print("no rows matched — check --scope / --reason / --title-like")
        return {}

    print(f"auditing {len(rows)} rows with {model} (scope={scope}, chars={chars})")
    results, start = [], time.time()
    for i, (aid, domain, title, url, body) in enumerate(rows, 1):
        t0 = time.time()
        label = classify(title, body, model, chars=chars)
        results.append({
            "id": aid, "domain": domain, "title": title, "url": url,
            "label": label, "secs": round(time.time() - t0, 2),
        })
        if i % 50 == 0:
            print(f"  {i}/{len(rows)}  elapsed {time.time()-start:.0f}s", flush=True)

    # Loud accounting. An earlier hand-rolled run reported half the true count because
    # it never tallied failures and never timed individual calls — a per-item duration of
    # 0.0s is the tell that a call never happened.
    assert len(results) == len(rows), "lost items during the run"
    tally = Counter(r["label"] for r in results)
    durs = sorted(r["secs"] for r in results)
    print(f"\ntotal {time.time()-start:.0f}s  |  per-item min/median/max: "
          f"{durs[0]:.2f}/{durs[len(durs)//2]:.2f}/{durs[-1]:.2f}s")
    print(f"outcomes: {dict(tally)}")
    if errors := sum(v for k, v in tally.items() if k.startswith("ERROR")):
        print(f"  ⚠ {errors} call(s) failed — the numbers below are incomplete")

    flagged = [r for r in results if r["label"] == "TEMPLATE"]
    pct = 100 * len(flagged) / len(results)
    print(f"\nTEMPLATE candidates: {len(flagged)}/{len(results)} = {pct:.1f}%")
    for domain, n in Counter(r["domain"] for r in flagged).most_common():
        print(f"  {n:>4}  {domain}")
    print("\ncandidates for HUMAN review (the LLM decides nothing):")
    for r in flagged[:40]:
        print(f"  [{(r['domain'] or '?')[:15]:<15}] {(r['title'] or '')[:74]}")
    if len(flagged) > 40:
        print(f"  … and {len(flagged)-40} more")

    if out:
        Path(out).write_text(json.dumps(results, indent=1))
        print(f"\nfull results -> {out}")
    return {"n": len(results), "flagged": len(flagged), "pct": round(pct, 1)}
