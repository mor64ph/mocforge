"""MOCForge Phase 6.3 - style / theme affinity, measured against frequency.

PRD 6.3: map an inventory to the theme it reads as, from its colour histogram.
Metric is top-3 theme accuracy. The deterministic baseline every model has to
beat is "always answer with the three commonest themes", which on a long-tailed
label set is stronger than it sounds.

Features are the set's colour histogram (share of pieces per colour) plus its
piece count. Deliberately not the set name: the name contains the theme in
plain text for much of the catalogue, so a model given it would score well and
have learnt nothing about the bricks.

Usage:
  python ml_style.py eval
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import train_test_split

DB_PATH = Path(__file__).resolve().parent / "data" / "mocforge.db"
SEED = 42
MIN_SETS_PER_THEME = 30
TOP_K = 3


def load() -> tuple[np.ndarray, np.ndarray, list[int]]:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA query_only = ON")

    colours = [r[0] for r in con.execute("SELECT id FROM colors ORDER BY id")]
    cidx = {c: i for i, c in enumerate(colours)}

    rows = con.execute(
        """SELECT i.set_num, ip.color_id, SUM(ip.quantity)
           FROM inventory_parts ip
           JOIN inventories i ON i.id = ip.inventory_id
           JOIN sets s        ON s.set_num = i.set_num
           WHERE ip.is_spare = 0
             AND i.version = (SELECT MAX(i2.version) FROM inventories i2
                              WHERE i2.set_num = i.set_num)
           GROUP BY i.set_num, ip.color_id"""
    ).fetchall()

    themes = dict(con.execute("SELECT set_num, theme_id FROM sets"))
    parts = dict(con.execute("SELECT set_num, num_parts FROM sets"))

    hist: dict[str, np.ndarray] = {}
    for set_num, colour, qty in rows:
        if colour not in cidx:
            continue
        v = hist.setdefault(set_num, np.zeros(len(colours), dtype=np.float32))
        v[cidx[colour]] += qty

    # Themes with a handful of sets cannot be learnt or fairly scored; they
    # would also inflate the label count without adding signal.
    counts = Counter(themes[s] for s in hist if s in themes)
    keep = {t for t, n in counts.items() if n >= MIN_SETS_PER_THEME}

    X, y = [], []
    for set_num, v in hist.items():
        theme = themes.get(set_num)
        if theme not in keep:
            continue
        total = v.sum()
        if total <= 0:
            continue
        X.append(np.concatenate([v / total, [np.log1p(parts.get(set_num) or 0)]]))
        y.append(theme)
    return np.asarray(X, dtype=np.float32), np.asarray(y), sorted(keep)


def evaluate() -> int:
    X, y, themes = load()
    print(f"sets: {len(X):,}  themes: {len(themes)}  features: {X.shape[1]}")

    Xtr, Xte, ytr, yte = train_test_split(
        X, y, test_size=0.2, random_state=SEED, stratify=y
    )

    freq = Counter(ytr)
    top3 = [t for t, _ in freq.most_common(TOP_K)]
    baseline = float(np.mean([t in top3 for t in yte]))

    clf = HistGradientBoostingClassifier(random_state=SEED, max_iter=200)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)
    order = np.argsort(-proba, axis=1)[:, :TOP_K]
    classes = clf.classes_
    model = float(
        np.mean([yte[i] in classes[order[i]] for i in range(len(yte))])
    )
    top1 = float(np.mean(clf.predict(Xte) == yte))

    print()
    print(f"  top-{TOP_K} baseline (3 commonest themes) : {baseline:.2%}")
    print(f"  top-{TOP_K} model    (colour histogram)   : {model:.2%}")
    print(f"  top-1 model                              : {top1:.2%}")
    print()

    if model > baseline:
        print(f"GATE PASSED: colour histogram beats frequency by "
              f"{(model - baseline) * 100:.1f} points. Ship 6.3.")
        return 0
    print(f"GATE FAILED: {model:.2%} vs {baseline:.2%}. PRD 6 says cut it.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "eval":
        raise SystemExit(__doc__)
    sys.exit(evaluate())
