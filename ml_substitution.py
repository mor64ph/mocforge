"""MOCForge Phase 6.1 - part substitution embeddings, measured against the graph.

PRD 6.1 sets the bar: hold out known A/M pairs, measure recall@10 of the true
alternate, and ship only if the model beats the relationship graph alone.

The held-out edge is removed from the graph before the baseline runs, so the
baseline is not trivially zero: union-find still reaches many held-out pairs
transitively via other edges. That surviving transitivity is the real number to
beat.

Training never sees a relationship label, which is what keeps it clear of the
R-pair trap in PRD 6.1: a model told that R means "equivalent" learns that a
Technic bush substitutes for a tyre. Here the only signal is which parts appear
in which set inventories.

Usage:
  python ml_substitution.py eval          # the Phase 6 gate for 6.1
  python ml_substitution.py eval --dims 256
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfTransformer
from sklearn.preprocessing import normalize

DB_PATH = Path(__file__).resolve().parent / "data" / "mocforge.db"

STRICT_RELS = ("M", "A")
NEVER_RELS = ("B",)
SEED = 42
TEST_FRACTION = 0.20
TOP_K = 10


def strict_edges(con: sqlite3.Connection) -> list[tuple[str, str]]:
    ph = ",".join("?" * len(STRICT_RELS))
    rows = con.execute(
        f"""SELECT child_part_num, parent_part_num FROM part_relationships
            WHERE rel_type IN ({ph})""",
        STRICT_RELS,
    ).fetchall()
    # B-parents are packs and assemblies; one pack is not one tile (PRD 6.1).
    ph2 = ",".join("?" * len(NEVER_RELS))
    assemblies = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT parent_part_num FROM part_relationships "
            f"WHERE rel_type IN ({ph2})",
            NEVER_RELS,
        )
    }
    return [
        (a, b) for a, b in rows if a != b and a not in assemblies and b not in assemblies
    ]


def part_set_matrix(con: sqlite3.Connection):
    """Parts x set-inventories, binary presence.

    Only the newest inventory of each real set: `inventories` is polymorphic
    (minifig inventories live there too) and 1,291 sets have several versions,
    so an unpinned query double-counts and mixes minifigs into the corpus.
    """
    rows = con.execute(
        """SELECT ip.part_num, i.id
           FROM inventory_parts ip
           JOIN inventories i ON i.id = ip.inventory_id
           JOIN sets s        ON s.set_num = i.set_num
           WHERE ip.is_spare = 0
             AND i.version = (SELECT MAX(i2.version) FROM inventories i2
                              WHERE i2.set_num = i.set_num)"""
    ).fetchall()

    parts = sorted({r[0] for r in rows})
    sets_ = sorted({r[1] for r in rows})
    pi = {p: i for i, p in enumerate(parts)}
    si = {s: i for i, s in enumerate(sets_)}

    data = np.ones(len(rows), dtype=np.float32)
    ri = np.fromiter((pi[r[0]] for r in rows), dtype=np.int32, count=len(rows))
    ci = np.fromiter((si[r[1]] for r in rows), dtype=np.int32, count=len(rows))
    m = csr_matrix((data, (ri, ci)), shape=(len(parts), len(sets_)))
    m.data[:] = 1.0
    return m, parts, pi


def union_find(edges: list[tuple[str, str]]) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return {p: find(p) for p in parent}


def evaluate(dims: int) -> int:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA query_only = ON")

    edges = strict_edges(con)
    rng = random.Random(SEED)
    rng.shuffle(edges)
    n_test = int(len(edges) * TEST_FRACTION)
    test, train = edges[:n_test], edges[n_test:]
    print(f"A/M edges: {len(edges):,}  train {len(train):,}  held-out {len(test):,}")

    matrix, parts, pi = part_set_matrix(con)
    print(f"corpus   : {matrix.shape[0]:,} parts x {matrix.shape[1]:,} set inventories")

    freq = Counter()
    for p, i in pi.items():
        freq[p] = matrix.indptr[i + 1] - matrix.indptr[i]

    # Only pairs whose both ends appear in the corpus are scoreable at all; a
    # part in no set inventory has no vector, and neither method could rank it.
    scoreable = [(a, b) for a, b in test if a in pi and b in pi]
    print(
        f"scoreable: {len(scoreable):,} of {len(test):,} held-out pairs "
        f"({len(scoreable) / max(len(test), 1):.1%} - the rest name parts in no set)"
    )
    if not scoreable:
        print("GATE: no scoreable pairs; cannot evaluate.")
        return 1

    classes = union_find(train)
    members: dict[str, list[str]] = defaultdict(list)
    for p, rep in classes.items():
        members[rep].append(p)

    baseline_hits = 0
    for a, b in scoreable:
        rep = classes.get(a)
        cands = [p for p in members.get(rep, []) if p != a] if rep else []
        cands.sort(key=lambda p: -freq.get(p, 0))
        if b in cands[:TOP_K]:
            baseline_hits += 1
    baseline = baseline_hits / len(scoreable)

    tfidf = TfidfTransformer(sublinear_tf=True)
    weighted = tfidf.fit_transform(matrix)
    svd = TruncatedSVD(n_components=dims, random_state=SEED)
    emb = normalize(svd.fit_transform(weighted))
    var = float(svd.explained_variance_ratio_.sum())
    print(f"embedding: {dims} dims, {var:.1%} of variance retained")

    model_hits = 0
    hybrid_hits = 0
    probe_idx = np.array([pi[a] for a, _ in scoreable])
    # Chunked so the score matrix never materialises at parts x parts.
    CHUNK = 256
    ranked: list[list[str]] = []
    for start in range(0, len(probe_idx), CHUNK):
        block = emb[probe_idx[start : start + CHUNK]]
        sims = block @ emb.T
        for row_i in range(sims.shape[0]):
            sims[row_i, probe_idx[start + row_i]] = -np.inf
            top = np.argpartition(-sims[row_i], TOP_K)[:TOP_K]
            top = top[np.argsort(-sims[row_i][top])]
            ranked.append([parts[j] for j in top])

    gained = lost = 0
    for (a, b), top in zip(scoreable, ranked):
        if b in top:
            model_hits += 1
        rep = classes.get(a)
        cands = [p for p in members.get(rep, []) if p != a] if rep else []
        cands.sort(key=lambda p: -freq.get(p, 0))
        merged = cands[:TOP_K] + [p for p in top if p not in cands]
        base_hit = b in cands[:TOP_K]
        hyb_hit = b in merged[:TOP_K]
        if hyb_hit:
            hybrid_hits += 1
        gained += hyb_hit and not base_hit
        lost += base_hit and not hyb_hit
    model = model_hits / len(scoreable)
    hybrid = hybrid_hits / len(scoreable)

    # Hybrid only appends where the graph offered fewer than TOP_K candidates,
    # so it cannot displace a baseline hit. `lost` proves that rather than
    # assuming it, and McNemar on the discordant pairs says whether the gain
    # is bigger than chance.
    from scipy.stats import binomtest

    disc = gained + lost
    p = binomtest(gained, disc, 0.5, alternative="greater").pvalue if disc else 1.0

    print()
    print(f"  recall@{TOP_K} baseline (graph only) : {baseline:.3%}")
    print(f"  recall@{TOP_K} part2vec (embeddings) : {model:.3%}")
    print(f"  recall@{TOP_K} hybrid (graph->embed) : {hybrid:.3%}")
    print(f"  hybrid vs baseline: +{gained} gained, -{lost} lost, "
          f"McNemar p={p:.2g}")
    print()

    if model > baseline:
        print(f"GATE PASSED: embeddings beat the graph by "
              f"{(model - baseline) * 100:.2f} points. Ship 6.1.")
        return 0
    print(f"GATE FAILED: embeddings do not beat the graph "
          f"({model:.3%} vs {baseline:.3%}).")
    if hybrid > baseline:
        print(f"  Hybrid does beat it ({hybrid:.3%}), by "
              f"{(hybrid - baseline) * 100:.2f} points - that is the shippable form.")
    else:
        print("  Hybrid does not rescue it either. PRD 6 says cut it.")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["eval"])
    ap.add_argument("--dims", type=int, default=128)
    args = ap.parse_args(argv)
    return evaluate(args.dims)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
