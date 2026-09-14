# Relevance rubric — how to annotate `relevant_docs`

This is the annotation guideline for the golden set. It ships with the dataset because a
set of labels is uninterpretable without the rule that produced them.

It exists because of a measured failure: an earlier annotation task (ARTICLE vs TEMPLATE)
was defined in two lines, and the annotator ended up judging three different things at
once while the model judged one. Agreement collapsed to 18%, and the number was
meaningless — it measured rubric mismatch, not model error. **A vague rubric produces
noise that looks like data.**

---

## The one question you are answering

> **Does this document contain the answer to *this* question?**

Relevance is a property of the **document–question pair**, never of the document alone.
The same article is relevant to one question and irrelevant to the next.

## The three levels

| Level | Test | Meaning |
|:---:|---|---|
| **2** | *"If this were the only document I had, could I answer the question correctly and completely?"* | Contains the answer |
| **1** | *"Does this give a necessary piece, or context without which the answer is wrong or incomplete — but not enough on its own?"* | Contributes |
| **0** | Everything else | Not relevant |

Level 1 is not a hedge for "I'm unsure". It is for genuinely **partial** evidence. Two
places it is the correct label:

* **Multi-document questions**, where by construction *no single* document holds the whole
  answer — every contributing document is a 1.
* **Necessary context**: the document establishes a fact the answer depends on (a prior
  injury, an earlier statement) without stating the answer itself.

If you are unsure between two levels, pick the **lower** one and move on. Consistency
matters more than any single item.

---

## What you are NOT judging

These are the three criteria that wrecked the earlier task. None of them belongs here:

| Not this | Why it's irrelevant |
|---|---|
| ❌ Is it good journalism? | A thin tabloid piece that contains the answer is a **2**. |
| ❌ Did extraction work cleanly? | Body quality is a corpus problem, tracked separately. Judge the text you can read. |
| ❌ Is it interesting / useful to me? | The question already fixed what matters. |
| ❌ Is it about the Premier League? | Scope decides where questions come *from*, never what counts as an answer. |
| ❌ Is it *about* the topic? | **The key one — see below.** |

### "About the topic" is not relevance

An article can be *about* a club and contain nothing that answers the question. An article
can be about something else entirely and answer it in one sentence.

* A 2,000-word Arsenal season review that never mentions the manager's departure →
  **0** for *"why did Arsenal dismiss their manager?"*
* A piece about Chelsea's recruitment that states in passing *"…after Arsenal sacked X
  over the board dispute"* → **2**, if that is the answer.

**Judge the sentences, not the subject.** This is the most common annotation error and the
one that most inflates `recall@k` into nonsense.

---

## Boundary cases, decided in advance

| Case | Level | Rule |
|---|:---:|---|
| Two outlets both fully answer | **2** and **2** | Redundancy is normal; don't pick a favourite. |
| Outlets report **different** figures, and the question asks *what was reported* | **2** each | The disagreement *is* the answer. |
| Outlets disagree, question asks the **actual** figure | judge each | Only the correct one is 2; the wrong report is 0 — unless the question is about the reporting. |
| **Preview predicting** a result; question asks the **actual** result | **0** | A prediction is not a fact. Never confuse forecast with outcome. |
| Published **after** the question's `as_of` date | **0** | Temporal questions are answered with what existed then. See below. |
| Near-duplicate of a document you already marked | **same level** | Annotate the canonical (`dup_of IS NULL`); its twins inherit. Never mark one copy and skip its identical sibling — that silently breaks `recall@k`. |
| Contains the answer but is factually wrong | **2** | Relevance is "does it contain the claimed answer", not "is the world correct". Note it in `notes` if it matters. |
| Answers a *different* question about the same event | **0** | Re-read the question you actually wrote. |

### Temporal questions

When a question carries an `as_of` date (*"what was reported about X in January?"*), a
document published after that date is **0**, even if it states the fact plainly. The whole
point of those questions is to test time-filtered retrieval — letting later documents
count would make the filter unmeasurable.

---

## Procedure

1. Read the question. Say the answer out loud before looking at candidates.
2. For each candidate document, apply the level-2 test first: *only this document — could
   I answer?*
3. If no, apply the level-1 test: *does it contribute a necessary piece?*
4. Otherwise 0.
5. Check the near-duplicate rule before finishing the item.
6. If you found yourself judging quality, usefulness, or topic — stop and re-apply step 2.

**Aim for completeness on 2s, not on 0s.** Missing a relevant document understates
`recall@k` for every configuration equally, but it also caps the maximum score anyone can
reach, so hunt for the 2s deliberately rather than stopping at the first one.

---

## Self-consistency check

Re-annotate ~20 items blind at least a few days after the first pass. Your agreement with
yourself is the **ceiling**: if you only match your own earlier labels 85% of the time, no
retrieval system can be meaningfully scored above ~85%. Record that number — it belongs in
the README next to the results table, and almost no portfolio project reports it.
