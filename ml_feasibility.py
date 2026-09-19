"""MOCForge Phase 6.2 - build feasibility predictor, measured against always-yes.

PRD 6.2: the tiler is expensive, so predict which (inventory, archetype) pairs
are hopeless and skip them. The metric is precision at 95% recall, and the
recall side is the one that matters - filtering out a feasible build is a
design the user never sees, which is far worse than a wasted tiler run.

The deterministic baseline is "run everything": 100% recall, and precision
equal to the base rate of feasible pairs. A model earns its place only by
lifting precision above that base rate while still keeping 95% of the builds.

Labels are self-generating: run the real fitter over sampled pairs and record
what it actually did. Features are the cheap inventory profile from roles.py
plus the archetype - deliberately everything you can know *without* tiling,
since a feature that needs the tiler defeats the purpose.

Usage:
  python ml_feasibility.py sample --sets 80    # build the label set (slow)
  python ml_feasibility.py eval
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mocforge.db"
DATA = ROOT / "data" / "feasibility.jsonl"
SEED = 42
FAMILIES = ("brick", "plate", "tile", "slope", "technic_beam", "technic_pin",
            "technic_axle", "wheel", "panel", "bracket")


def sample(n_sets: int) -> int:
    from generate import TEMPLATES, Fitter
    from inventory import Catalogue
    from ldraw import LDrawLibrary
    from roles import Taxonomy

    con = sqlite3.connect(DB_PATH)
    pool = [
        r[0]
        for r in con.execute(
            """SELECT s.set_num FROM sets s
               WHERE s.num_parts BETWEEN 40 AND 1500
                 AND s.set_num IN (SELECT set_num FROM inventories)"""
        )
    ]
    rng = random.Random(SEED)
    rng.shuffle(pool)
    chosen = pool[:n_sets]

    cat, lib = Catalogue(), LDrawLibrary()
    fitter = Fitter(cat, lib)
    tax = Taxonomy(cat, lib)

    written = 0
    with DATA.open("w", encoding="utf-8") as fh:
        for i, set_num in enumerate(chosen, 1):
            try:
                lots = cat.combine(set_num)
                prof = tax.profile(set_num)
            except Exception:
                continue
            feats = {
                "pieces": prof.pieces,
                "lots": prof.lots,
                "geometry_pieces": prof.geometry_pieces,
                "structural_area": prof.structural_area,
            }
            for fam in FAMILIES:
                feats[f"n_{fam}"] = prof.by_family.get(fam, 0)
                feats[f"a_{fam}"] = prof.area_by_family.get(fam, 0)

            for name, tpl in TEMPLATES.items():
                for variant in tpl.variants or ({},):
                    try:
                        build = fitter.fit(tpl, lots, variant)
                        ok = bool(build.buildable)
                    except Exception:
                        ok = False
                    fh.write(json.dumps({
                        "set_num": set_num,
                        "archetype": f"{name}-{variant.get('name', '')}",
                        "template": name,
                        "feasible": ok,
                        **feats,
                    }) + "\n")
                    written += 1
            print(f"  [{i}/{len(chosen)}] {set_num}: {written} pairs", flush=True)
    print(f"wrote {written} labelled pairs -> {DATA}")
    return 0


def evaluate() -> int:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupShuffleSplit

    if not DATA.exists():
        print(f"no label set at {DATA}; run `sample` first.")
        return 1
    rows = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l]
    if not rows:
        print("label set is empty.")
        return 1

    templates = sorted({r["template"] for r in rows})
    feat_names = [k for k in rows[0]
                  if k not in ("set_num", "archetype", "template", "feasible")]

    X = np.array([
        [r[k] for k in feat_names] + [1.0 if r["template"] == t else 0.0
                                      for t in templates]
        for r in rows
    ], dtype=np.float32)
    y = np.array([r["feasible"] for r in rows], dtype=bool)
    groups = np.array([r["set_num"] for r in rows])

    base_rate = y.mean()
    print(f"pairs: {len(y):,} from {len(set(groups)):,} sets  "
          f"feasible: {base_rate:.1%}")

    # Split by set, not by row: the same inventory appearing in train and test
    # would leak, since every archetype for one set shares its features.
    gss = GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=SEED)
    tr, te = next(gss.split(X, y, groups))

    clf = HistGradientBoostingClassifier(random_state=SEED, max_iter=300)
    clf.fit(X[tr], y[tr])
    proba = clf.predict_proba(X[te])[:, 1]
    yte = y[te]

    te_base = yte.mean()
    # Lowest threshold that still keeps 95% of genuinely feasible builds.
    order = np.argsort(-proba)
    target = 0.95
    thresh, prec_at = None, None
    for cut in np.unique(proba):
        keep = proba >= cut
        if keep.sum() == 0:
            continue
        rec = (yte & keep).sum() / max(yte.sum(), 1)
        if rec >= target:
            prec = (yte & keep).sum() / keep.sum()
            if thresh is None or prec > prec_at:
                thresh, prec_at = cut, prec
    saved = 1.0 - (proba >= thresh).mean() if thresh is not None else 0.0

    print()
    print(f"  baseline precision (run everything) : {te_base:.1%}  recall 100%")
    if prec_at is None:
        print("  model cannot reach 95% recall at any threshold.")
        print("GATE FAILED: PRD 6 says cut it.")
        return 1
    print(f"  model precision at {target:.0%} recall     : {prec_at:.1%}  "
          f"(threshold {thresh:.3g})")
    print(f"  tiler runs skipped                  : {saved:.1%}")
    print()

    if prec_at > te_base:
        print(f"GATE PASSED: precision lifted {(prec_at - te_base) * 100:.1f} "
              f"points over always-run, skipping {saved:.0%} of tiler work. Ship 6.2.")
        return 0
    print(f"GATE FAILED: {prec_at:.1%} vs {te_base:.1%}. PRD 6 says cut it.")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["sample", "eval"])
    ap.add_argument("--sets", type=int, default=80)
    args = ap.parse_args(argv)
    return sample(args.sets) if args.command == "sample" else evaluate()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
