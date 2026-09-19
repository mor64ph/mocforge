"""MOCForge Phase 6.2 - build feasibility predictor, measured against always-yes.

PRD 6.2: the tiler is expensive, so predict which (inventory, archetype) pairs
are hopeless and skip them. The metric is precision at 95% recall, and the
recall side is the one that matters - filtering out a feasible build is a
design the user never sees, which is far worse than a wasted tiler run.

**This model is cut. The file is kept because the finding is worth keeping.**

It passes its statistical gate comfortably: 68.8% precision at 95% recall,
against 28.0% for running everything and 32.9% for the best single-feature
rule. It is still cut, because the premise above is false. Tiling all 13
archetypes costs 3.95s cold and 0.14s warm, while the features here cost 4.25s
to compute - `Taxonomy.profile()` loads the same geometry the tiler needs - on
top of 13.49s to import sklearn. Wired in behind a flag it ran 3.5x slower.
The inputs are dearer than the output and no threshold changes that.

So do not re-add a feasibility pre-filter on the strength of the accuracy
numbers alone. Re-time the tiler first; only a genuinely cheap feature path
(one that does not need geometry) could change the conclusion.

Two baselines are reported, because "run everything" is trivially beaten: the
base rate, and the best single feature-and-threshold rule chosen on train. A
model only earns its complexity by clearing the second.

Usage:
  python ml_feasibility.py sample --sets 300   # label set; slow, runs the tiler
  python ml_feasibility.py refeature           # new features, no re-tiling
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


def _families() -> tuple[str, ...]:
    """Every family the taxonomy knows, not a hand-picked ten.

    The first pass used ten and left 23 on the floor - tyre, gear, panel and
    the modified-plate families all carry archetype signal.
    """
    from roles import FAMILY_RULES

    return tuple(sorted(FAMILY_RULES))


FAMILIES = _families()


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


def refeature() -> int:
    """Recompute features for existing labels without re-running the tiler.

    Labels are the irreplaceable half - each one is a real fit - so changing
    the feature set must never cost another tiling pass. Profiling is not
    cheap in absolute terms (4.25s a set; that is the finding that cut this
    model), but it is cheap next to re-labelling.
    """
    from inventory import Catalogue
    from ldraw import LDrawLibrary
    from roles import Taxonomy

    if not DATA.exists():
        print(f"no label set at {DATA}; run `sample` first.")
        return 1
    rows = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l]
    keep = [{k: r[k] for k in ("set_num", "archetype", "template", "feasible")}
            for r in rows]

    tax = Taxonomy(Catalogue(), LDrawLibrary())
    cache: dict[str, dict] = {}
    for i, set_num in enumerate(sorted({r["set_num"] for r in keep}), 1):
        try:
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
        cache[set_num] = feats
        if i % 25 == 0:
            print(f"  profiled {i}", flush=True)

    with DATA.open("w", encoding="utf-8") as fh:
        n = 0
        for r in keep:
            f = cache.get(r["set_num"])
            if f is None:
                continue
            fh.write(json.dumps({**r, **f}) + "\n")
            n += 1
    print(f"rewrote {n} rows with {len(FAMILIES)} families -> {DATA}")
    return 0


def _stump(Xtr, ytr, Xte, yte, target: float) -> tuple[float, str, int]:
    """Best single feature-and-threshold rule, chosen on train, scored on test.

    This is the honest deterministic comparator. "Run everything" is trivially
    beaten; a model only earns its complexity by beating the best one-feature
    rule too.
    """
    best = (0.0, "none", -1)
    for j in range(Xtr.shape[1]):
        col_tr, col_te = Xtr[:, j], Xte[:, j]
        for cut in np.unique(np.quantile(col_tr, np.linspace(0, 1, 25))):
            for sense in (1, -1):
                keep_tr = (col_tr >= cut) if sense > 0 else (col_tr <= cut)
                if keep_tr.sum() == 0:
                    continue
                if (ytr & keep_tr).sum() / max(ytr.sum(), 1) < target:
                    continue
                keep_te = (col_te >= cut) if sense > 0 else (col_te <= cut)
                if keep_te.sum() == 0:
                    continue
                prec = (yte & keep_te).sum() / keep_te.sum()
                if prec > best[0]:
                    best = (prec, f"feature[{j}] {'>=' if sense > 0 else '<='} {cut:g}", j)
    return best


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

    stump_prec, stump_desc, stump_j = _stump(X[tr], y[tr], X[te], yte, target)
    all_names = feat_names + [f"is_{t}" for t in templates]
    stump_name = all_names[stump_j] if stump_j >= 0 else "none"

    print()
    print(f"  baseline precision (run everything) : {te_base:.1%}  recall 100%")
    print(f"  best single-feature rule            : {stump_prec:.1%}  "
          f"({stump_desc.replace(f'feature[{stump_j}]', stump_name)})")
    if prec_at is None:
        print("  model cannot reach 95% recall at any threshold.")
        print("GATE FAILED: PRD 6 says cut it.")
        return 1
    print(f"  model precision at {target:.0%} recall     : {prec_at:.1%}  "
          f"(threshold {thresh:.3g})")
    print(f"  tiler runs skipped                  : {saved:.1%}")
    print()

    if prec_at > max(te_base, stump_prec):
        print(f"STATISTICAL GATE PASSED: precision lifted "
              f"{(prec_at - te_base) * 100:.1f} points over always-run and "
              f"{(prec_at - stump_prec) * 100:.1f} over the best one-feature rule, "
              f"skipping {saved:.0%} of tiler work.")
        print("PRODUCT VERDICT: still cut. The skipped work is worth 0.14s warm / "
              "3.95s cold; these features cost 4.25s to compute. See the module "
              "docstring before re-adding a pre-filter.")
        return 0
    if stump_prec >= prec_at:
        print(f"GATE FAILED for the model: a single rule ({stump_name}) matches or "
              f"beats it, {stump_prec:.1%} vs {prec_at:.1%}. Ship the rule, not the "
              f"model - it needs no sklearn at runtime.")
        return 1
    print(f"GATE FAILED: {prec_at:.1%} vs {te_base:.1%}. PRD 6 says cut it.")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["sample", "refeature", "eval"])
    ap.add_argument("--sets", type=int, default=80)
    args = ap.parse_args(argv)
    if args.command == "sample":
        return sample(args.sets)
    if args.command == "refeature":
        return refeature()
    return evaluate()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
