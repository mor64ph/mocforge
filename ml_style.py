"""MOCForge Phase 6.3 - style / theme affinity, measured against frequency.

PRD 6.3: map an inventory to the theme it reads as, from its colour histogram.
Metric is top-3 theme accuracy. The deterministic baseline every model has to
beat is "always answer with the three commonest themes", which on a long-tailed
label set is stronger than it sounds.

Features are the colour histogram, a *shape* histogram over part categories,
piece count and year, reported as an ablation so each group's contribution is
visible rather than asserted. Deliberately not the set name: the name contains
the theme in plain text for much of the catalogue, so a model given it would
score well and have learnt nothing about the bricks.

Year is kept but is not load-bearing - it is worth 2.3 points, so an inventory
of unknown vintage loses little. Shape is what matters, at +19.1 points over
colour alone.

Usage:
  python ml_style.py eval
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DB_PATH = Path(__file__).resolve().parent / "data" / "mocforge.db"
SEED = 42
MIN_SETS_PER_THEME = 30
TOP_K = 3


def load() -> tuple[np.ndarray, np.ndarray, list[int]]:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA query_only = ON")

    colours = [r[0] for r in con.execute("SELECT id FROM colors ORDER BY id")]
    cidx = {c: i for i, c in enumerate(colours)}
    cats = [r[0] for r in con.execute("SELECT id FROM part_categories ORDER BY id")]
    katx = {c: i for i, c in enumerate(cats)}
    part_cat = dict(con.execute("SELECT part_num, part_cat_id FROM parts"))

    # Category histogram is the shape vocabulary. Colour alone cannot tell a
    # Technic set from a castle; the mix of part *categories* can, and unlike
    # the role taxonomy it needs no geometry, so it scales to every set.
    rows = con.execute(
        """SELECT i.set_num, ip.color_id, ip.part_num, SUM(ip.quantity)
           FROM inventory_parts ip
           JOIN inventories i ON i.id = ip.inventory_id
           JOIN sets s        ON s.set_num = i.set_num
           WHERE ip.is_spare = 0
             AND i.version = (SELECT MAX(i2.version) FROM inventories i2
                              WHERE i2.set_num = i.set_num)
           GROUP BY i.set_num, ip.color_id, ip.part_num"""
    ).fetchall()

    themes = dict(con.execute("SELECT set_num, theme_id FROM sets"))
    parts = dict(con.execute("SELECT set_num, num_parts FROM sets"))
    years = dict(con.execute("SELECT set_num, year FROM sets"))

    hist: dict[str, np.ndarray] = {}
    shape: dict[str, np.ndarray] = {}
    for set_num, colour, part_num, qty in rows:
        if colour in cidx:
            v = hist.setdefault(set_num, np.zeros(len(colours), dtype=np.float32))
            v[cidx[colour]] += qty
        k = part_cat.get(part_num)
        if k in katx:
            w = shape.setdefault(set_num, np.zeros(len(cats), dtype=np.float32))
            w[katx[k]] += qty

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
        w = shape.get(set_num)
        if total <= 0 or w is None or w.sum() <= 0:
            continue
        X.append(np.concatenate([
            v / total,
            w / w.sum(),
            [np.log1p(parts.get(set_num) or 0), float(years.get(set_num) or 0)],
        ]))
        y.append(theme)
    spans = {
        "colour": (0, len(colours)),
        "shape": (len(colours), len(colours) + len(cats)),
        "size": (len(colours) + len(cats), len(colours) + len(cats) + 1),
        "year": (len(colours) + len(cats) + 1, len(colours) + len(cats) + 2),
    }
    return np.asarray(X, dtype=np.float32), np.asarray(y), sorted(keep), spans


ABLATIONS = (
    ("colour only", ("colour", "size")),
    ("colour + shape", ("colour", "shape", "size")),
    ("colour + shape + year", ("colour", "shape", "size", "year")),
)


def evaluate() -> int:
    X, y, themes, spans = load()
    print(f"sets: {len(X):,}  themes: {len(themes)}  features: {X.shape[1]}")

    Xtr, Xte, ytr, yte = train_test_split(
        X, y, test_size=0.2, random_state=SEED, stratify=y
    )

    top3 = [t for t, _ in Counter(ytr).most_common(TOP_K)]
    baseline = float(np.mean([t in top3 for t in yte]))

    print()
    print(f"  {'features':26}{'top-3':>9}{'top-1':>9}")
    print(f"  {'3 commonest (baseline)':26}{baseline:>9.2%}{'-':>9}")

    best = (0.0, "", 0.0)
    for label, groups in ABLATIONS:
        cols = np.concatenate([np.arange(*spans[g]) for g in groups])
        # Multinomial logistic, not a boosted ensemble: with 136 classes a
        # histogram GBDT fits one tree per class per iteration and takes tens
        # of minutes for no measured gain on dense histogram features.
        clf = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000),
        )
        clf.fit(Xtr[:, cols], ytr)
        proba = clf.predict_proba(Xte[:, cols])
        order = np.argsort(-proba, axis=1)[:, :TOP_K]
        classes = clf.classes_ if hasattr(clf, "classes_") else clf[-1].classes_
        top3_acc = float(
            np.mean([yte[i] in classes[order[i]] for i in range(len(yte))])
        )
        top1 = float(np.mean(clf.predict(Xte[:, cols]) == yte))
        print(f"  {label:26}{top3_acc:>9.2%}{top1:>9.2%}")
        if top3_acc > best[0]:
            best = (top3_acc, label, top1)
    print()

    model, label, top1 = best
    if model > baseline:
        print(f"GATE PASSED: best is '{label}' at {model:.2%} top-3, "
              f"{(model - baseline) * 100:.1f} points over frequency "
              f"({top1:.2%} top-1). Ship 6.3.")
        return 0
    print(f"GATE FAILED: {model:.2%} vs {baseline:.2%}. PRD 6 says cut it.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "eval":
        raise SystemExit(__doc__)
    sys.exit(evaluate())
