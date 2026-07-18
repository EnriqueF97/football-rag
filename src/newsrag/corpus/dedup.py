"""Near-duplicate detection via MinHash + LSH.

Syndicated wire copies are the same story republished across many domains with small
edits. URL dedup misses them (different URLs) and exact content hashing misses them (the
text differs slightly) — this corpus already contains a live example: the same Salah
story on ``dailypost.ng`` and ``nigerianeye.com``, with different ``content_hash`` values.

Left undetected they corrupt ``recall@k`` in *both* directions: retrieval that returns 15
copies of the right story scores as 14 misses, and a top-5 filled with copies of one
article looks like five relevant results while holding one piece of information.

Approach:

1. Shingle each body into overlapping word n-grams (phrases, not words — any two football
   articles share "goal" and "club"; only copied text shares whole phrases).
2. Build a MinHash signature per article: P(signatures match) == Jaccard similarity.
3. Index signatures in an LSH so only plausible pairs are ever compared (O(n), not O(n²)).
4. Take **connected components** as clusters (A~B and B~C ⇒ one cluster).
5. Point every non-canonical member's ``dup_of`` at the cluster's canonical article.

Canonical choice is deterministic so evaluation stays reproducible across runs:

    valid body → longest body → earliest published_at → lowest id

Nothing is deleted. Re-running with a different ``--threshold`` recomputes from scratch.
"""

from __future__ import annotations

import re
from collections import defaultdict

from datasketch import MinHash, MinHashLSH

from newsrag.corpus import db

# LSH's threshold is an S-curve, not a hard cutoff: a pair sitting *at* the threshold is
# roughly a coin flip. Empirically our known syndicated pair estimates at Jaccard 0.80 and
# was MISSED at threshold=0.80, caught at 0.75/0.70/0.60. So set the threshold below the
# similarity you actually intend to catch.
# NOTE: tuned on a 5-article smoke corpus — re-validate on the full corpus using the
# cluster-size report from `corpus stats`.
DEFAULT_THRESHOLD = 0.75
NUM_PERM = 128  # signature length; error ≈ 1/sqrt(128) ≈ 9%
SHINGLE_SIZE = 5  # words per shingle; larger = more precise, less recall


def shingles(text: str, size: int = SHINGLE_SIZE) -> set[str]:
    """Split text into overlapping word n-grams."""
    words = re.sub(r"\s+", " ", text.lower()).strip().split()
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def minhash(text: str, num_perm: int = NUM_PERM) -> MinHash:
    """Build a MinHash signature for one document."""
    m = MinHash(num_perm=num_perm)
    for sh in shingles(text):
        m.update(sh.encode("utf-8"))
    return m


class _UnionFind:
    """Tiny union-find, used to turn similar *pairs* into connected *clusters*."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def dedup(
    db_path=db.DEFAULT_DB_PATH,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = NUM_PERM,
) -> dict:
    """Cluster near-duplicates and mark `dup_of`. Idempotent; safe to re-run."""
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, body_full, body_valid, published_at FROM articles "
            "WHERE body_full IS NOT NULL"
        ).fetchall()
        if not rows:
            print("no extracted bodies yet — run `corpus extract` first")
            return {}

        lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        sigs: dict[int, MinHash] = {}
        for article_id, body, _valid, _pub in rows:
            m = minhash(body, num_perm)
            sigs[article_id] = m
            lsh.insert(str(article_id), m)

        uf = _UnionFind()
        for article_id, sig in sigs.items():
            for other in lsh.query(sig):
                if int(other) != article_id:
                    uf.union(article_id, int(other))

        clusters: dict[int, list[int]] = defaultdict(list)
        for article_id in sigs:
            clusters[uf.find(article_id)].append(article_id)

        # (valid, length, published_at) per article, for the canonical tie-break chain.
        meta = {
            article_id: (valid, len(body or ""), pub or "")
            for article_id, body, valid, pub in rows
        }

        updates: list[tuple[int, int]] = []
        sizes: list[int] = []
        for members in clusters.values():
            if len(members) < 2:
                continue
            sizes.append(len(members))
            canonical = min(
                members,
                key=lambda i: (
                    0 if meta[i][0] else 1,  # 1. a valid body wins
                    -meta[i][1],             # 2. longest body
                    meta[i][2],              # 3. earliest published_at
                    i,                       # 4. lowest id (determinism)
                ),
            )
            updates.extend((canonical, m) for m in members if m != canonical)

        # Recompute from scratch so a new --threshold fully re-clusters.
        conn.execute("UPDATE articles SET dup_of = NULL")
        conn.executemany("UPDATE articles SET dup_of = ? WHERE id = ?", updates)
        conn.commit()
    finally:
        conn.close()

    stats = {
        "articles_with_bodies": len(rows),
        "duplicate_clusters": len(sizes),
        "marked_as_duplicate": len(updates),
        "canonical_articles": len(rows) - len(updates),
        "largest_cluster": max(sizes, default=0),
    }
    for key, value in stats.items():
        print(f"{key.replace('_', ' '):24s} {value}")
    if stats["largest_cluster"] >= 20:
        print(
            f"  ⚠ largest cluster is {stats['largest_cluster']} articles — transitive "
            f"chaining may be merging distinct stories; consider a higher --threshold"
        )
    return stats
