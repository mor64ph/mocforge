"""MOCForge Phase 6.1 - part substitution, graph vs embeddings, measured.

PRD 6.1 sets the bar: hold out known A/M pairs, measure recall@10 of the true
alternate, and ship only what beats the relationship graph alone.

The held-out edge is removed before the baseline runs, so the baseline is not
trivially zero: union-find still reaches many held-out pairs transitively. That
surviving transitivity is the real number to beat.

Two findings from the first pass (2026-09-20) shape this one:

* **Plain co-occurrence is the wrong signal.** Alternates are used *in place of*
  each other, so they rarely share a set. A model asking "which parts appear in
  the same sets" learns complements. The fix is second-order similarity - two
  parts are alike if they appear alongside *similar other parts*, not if they
  appear together. For a truncated SVD that is one parameter: cosine between
  rows of `U S^p`, where p=1 is first-order and p=2 is second-order, because
  the row inner products of `MM^T` reduce exactly to those of `U S^2`.
* **Category is a near-free filter.** 99.0% of A/M pairs share a part category,
  so constraining candidates to the probe's category discards almost no true
  pair and removes most of the field. Names are not usable the same way: only
  4.6% of pairs share one.

Training never sees a relationship label, which keeps it clear of the R-pair
trap in PRD 6.1: a model told R means "equivalent" learns that a Technic bush
substitutes for a tyre.

Usage:
  python ml_substitution.py eval            # the full comparison
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
EXPONENTS = (0.0, 1.0, 2.0)


def strict_edges(con: sqlite3.Connection) -> list[tuple[str, str]]:
    ph = ",".join("?" * len(STRICT_RELS))
    rows = con.execute(
        f"""SELECT child_part_num, parent_part_num FROM part_relationships
            WHERE rel_type IN ({ph})""",
        STRICT_RELS,
    ).fetchall()
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
    `is_spare` is INTEGER here - the text True/False is the raw CSV form.
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

    ri = np.fromiter((pi[r[0]] for r in rows), dtype=np.int32, count=len(rows))
    ci = np.fromiter((si[r[1]] for r in rows), dtype=np.int32, count=len(rows))
    m = csr_matrix(
        (np.ones(len(rows), dtype=np.float32), (ri, ci)),
        shape=(len(parts), len(sets_)),
    )
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


def rank_embeddings(emb, probe_idx, cat_codes, restrict: bool, k: int):
    """Top-k neighbour indices per probe, optionally within the probe's category."""
    out: list[np.ndarray] = []
    CHUNK = 128
    for start in range(0, len(probe_idx), CHUNK):
        idx = probe_idx[start : start + CHUNK]
        sims = emb[idx] @ emb.T
        for r, gi in enumerate(idx):
            row = sims[r]
            row[gi] = -np.inf
            if restrict:
                row = np.where(cat_codes == cat_codes[gi], row, -np.inf)
            top = np.argpartition(-row, k)[:k]
            out.append(top[np.argsort(-row[top])])
    return out


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

    cats = dict(con.execute("SELECT part_num, part_cat_id FROM parts"))
    cat_codes = np.array([cats.get(p, -1) for p in parts], dtype=np.int32)

    freq = Counter({p: matrix.indptr[i + 1] - matrix.indptr[i] for p, i in pi.items()})

    scoreable = [(a, b) for a, b in test if a in pi and b in pi]
    print(
        f"scoreable: {len(scoreable):,} of {len(test):,} held-out pairs "
        f"({len(scoreable) / max(len(test), 1):.1%})"
    )
    if not scoreable:
        print("GATE: no scoreable pairs; cannot evaluate.")
        return 1

    classes = union_find(train)
    members: dict[str, list[str]] = defaultdict(list)
    for p, rep in classes.items():
        members[rep].append(p)

    def graph_candidates(a: str) -> list[str]:
        rep = classes.get(a)
        c = [p for p in members.get(rep, []) if p != a] if rep else []
        c.sort(key=lambda p: -freq.get(p, 0))
        return c

    baseline = np.mean([b in graph_candidates(a)[:TOP_K] for a, b in scoreable])

    weighted = TfidfTransformer(sublinear_tf=True).fit_transform(matrix)
    svd = TruncatedSVD(n_components=dims, random_state=SEED)
    us = svd.fit_transform(weighted)
    sing = svd.singular_values_.copy()
    sing[sing == 0] = 1e-12
    u = us / sing
    print(f"embedding: {dims} dims, "
          f"{svd.explained_variance_ratio_.sum():.1%} of variance retained")

    probe_idx = np.array([pi[a] for a, _ in scoreable])
    results: dict[tuple[float, bool], tuple[float, float, int, int]] = {}

    for p in EXPONENTS:
        emb = normalize(u * (sing**p))
        for restrict in (False, True):
            ranked = rank_embeddings(emb, probe_idx, cat_codes, restrict, TOP_K)
            hits = gained = lost = 0
            hyb_hits = 0
            for (a, b), top in zip(scoreable, ranked):
                names = [parts[j] for j in top]
                if b in names:
                    hits += 1
                cand = graph_candidates(a)
                merged = cand[:TOP_K] + [n for n in names if n not in cand]
                base_hit = b in cand[:TOP_K]
                hyb_hit = b in merged[:TOP_K]
                hyb_hits += hyb_hit
                gained += hyb_hit and not base_hit
                lost += base_hit and not hyb_hit
            results[(p, restrict)] = (
                hits / len(scoreable), hyb_hits / len(scoreable), gained, lost
            )

    print()
    print(f"  {'config':34}{'embed':>9}{'hybrid':>9}{'+gain':>7}{'-loss':>7}")
    print(f"  {'graph only (baseline)':34}{'-':>9}{baseline:>8.2%}{'-':>7}{'-':>7}")
    for (p, restrict), (emb_r, hyb_r, g, l) in results.items():
        order = {0.0: "whitened", 1.0: "first-order", 2.0: "second-order"}[p]
        label = f"{order}, {'same-category' if restrict else 'all parts'}"
        print(f"  {label:34}{emb_r:>9.2%}{hyb_r:>9.2%}{g:>7}{l:>7}")
    print()

    best_key = max(results, key=lambda k: results[k][1])
    best_emb, best_hyb, g, l = results[best_key]
    p, restrict = best_key
    from scipy.stats import binomtest

    disc = g + l
    pv = binomtest(g, disc, 0.5, alternative="greater").pvalue if disc else 1.0
    label = (f"{ {0.0:'whitened',1.0:'first-order',2.0:'second-order'}[p] }, "
             f"{'same-category' if restrict else 'all parts'}")

    print(f"best hybrid: {label} -> {best_hyb:.2%} vs graph {baseline:.2%} "
          f"(+{g}/-{l}, p={pv:.2g})")
    if best_emb > baseline:
        print(f"Embeddings alone beat the graph ({best_emb:.2%} vs {baseline:.2%}).")
    else:
        print(f"Embeddings alone still lose ({best_emb:.2%} vs {baseline:.2%}); "
              f"the graph-first hybrid is the shippable form.")
    if best_hyb > baseline:
        print(f"GATE PASSED: ship 6.1 as '{label}' hybrid.")
        return 0
    print("GATE FAILED: nothing beats the graph. PRD 6 says cut it.")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["eval"])
    ap.add_argument("--dims", type=int, default=128)
    args = ap.parse_args(argv)
    return evaluate(args.dims)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
