"""MOCForge Phase 6.4 - difficulty / age estimator.

PRD 6.4 asks for MAE in years against published age ratings. The Rebrickable
bulk dump has no such column and neither does its `/api/v3/` API - the whole
permitted surface is silent on age - so the label has to come from elsewhere.
Brickset publishes it as `ageRange {min, max}` and offers a documented,
key-authenticated JSON API.

That keeps the project's sourcing rule intact in spirit as well as letter: this
is a documented API called with a key, not a scraped HTML page.

Fetching is by *year*, not by set. `getSets` accepts `pageSize` up to 500 and
only that method counts against the daily key quota, so paging through years
costs on the order of 70 calls for the whole catalogue instead of 28,356.

    1. Get a key:  https://brickset.com/tools/webservices/requestkey
    2. Save it to: data/api/brickset_key.txt   (gitignored, one line)
    3. python ml_difficulty.py fetch
    4. python ml_difficulty.py eval

Usage:
  python ml_difficulty.py fetch [--from 1990] [--to 2025]
  python ml_difficulty.py eval
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mocforge.db"
KEY_PATH = ROOT / "data" / "api" / "brickset_key.txt"
AGES = ROOT / "data" / "ages.json"
ENDPOINT = "https://brickset.com/api/v3.asmx/getSets"
SEED = 42
PAGE_SIZE = 500


def _key() -> str | None:
    if not KEY_PATH.exists():
        return None
    k = KEY_PATH.read_text(encoding="utf-8").strip()
    return k or None


def fetch(year_from: int, year_to: int) -> int:
    key = _key()
    if not key:
        print(f"No Brickset API key at {KEY_PATH}.")
        print("Request one at https://brickset.com/tools/webservices/requestkey")
        print("then save it there as a single line. Nothing was fetched.")
        return 1

    out: dict[str, dict] = {}
    if AGES.exists():
        out = json.loads(AGES.read_text(encoding="utf-8"))
        print(f"resuming from {len(out):,} cached sets")

    for year in range(year_from, year_to + 1):
        page = 1
        while True:
            params = json.dumps(
                {"year": str(year), "pageSize": PAGE_SIZE, "pageNumber": page}
            )
            body = urllib.parse.urlencode(
                {"apiKey": key, "userHash": "", "params": params}
            ).encode()
            try:
                with urllib.request.urlopen(ENDPOINT, data=body, timeout=60) as r:
                    payload = json.load(r)
            except Exception as exc:
                print(f"  {year} p{page}: request failed ({exc}); stopping.")
                AGES.write_text(json.dumps(out), encoding="utf-8")
                return 1

            if payload.get("status") != "success":
                print(f"  {year} p{page}: API said {payload.get('status')} "
                      f"- {payload.get('message')}")
                AGES.write_text(json.dumps(out), encoding="utf-8")
                return 1

            sets = payload.get("sets") or []
            for s in sets:
                num = f"{s.get('number')}-{s.get('numberVariant')}"
                ar = s.get("ageRange") or {}
                lo, hi = ar.get("min"), ar.get("max")
                if lo is None and hi is None:
                    continue
                out[num] = {"min": lo, "max": hi, "pieces": s.get("pieces"),
                            "theme": s.get("theme"), "year": s.get("year")}
            print(f"  {year} p{page}: {len(sets)} sets, {len(out):,} with ages",
                  flush=True)
            if len(sets) < PAGE_SIZE:
                break
            page += 1
            time.sleep(0.5)

    AGES.write_text(json.dumps(out), encoding="utf-8")
    print(f"wrote {len(out):,} age-rated sets -> {AGES}")
    return 0


def evaluate() -> int:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import train_test_split

    if not AGES.exists():
        print(f"no age labels at {AGES}; run `fetch` first.")
        return 1
    ages = json.loads(AGES.read_text(encoding="utf-8"))

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA query_only = ON")
    cats = [r[0] for r in con.execute("SELECT id FROM part_categories ORDER BY id")]
    katx = {c: i for i, c in enumerate(cats)}
    part_cat = dict(con.execute("SELECT part_num, part_cat_id FROM parts"))

    rows = con.execute(
        """SELECT i.set_num, ip.part_num, SUM(ip.quantity)
           FROM inventory_parts ip
           JOIN inventories i ON i.id = ip.inventory_id
           JOIN sets s        ON s.set_num = i.set_num
           WHERE ip.is_spare = 0
             AND i.version = (SELECT MAX(i2.version) FROM inventories i2
                              WHERE i2.set_num = i.set_num)
           GROUP BY i.set_num, ip.part_num"""
    ).fetchall()
    meta = {r[0]: r[1:] for r in con.execute(
        "SELECT set_num, num_parts, year, theme_id FROM sets")}

    shape: dict[str, np.ndarray] = {}
    for set_num, part_num, qty in rows:
        k = part_cat.get(part_num)
        if k in katx:
            w = shape.setdefault(set_num, np.zeros(len(cats), dtype=np.float32))
            w[katx[k]] += qty

    X, y = [], []
    for set_num, w in shape.items():
        label = ages.get(set_num)
        m = meta.get(set_num)
        if not label or not m or w.sum() <= 0:
            continue
        lo, hi = label.get("min"), label.get("max")
        target = lo if hi is None else (hi if lo is None else (lo + hi) / 2)
        if target is None:
            continue
        X.append(np.concatenate([
            w / w.sum(),
            [np.log1p(m[0] or 0), float(m[1] or 0), float(m[2] or 0)],
        ]))
        y.append(float(target))

    if len(X) < 200:
        print(f"only {len(X)} sets have both an age label and an inventory; "
              f"too few to evaluate. Fetch more years.")
        return 1
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32)
    print(f"sets with age label and inventory: {len(y):,}  "
          f"mean age {y.mean():.1f}, sd {y.std():.1f}")

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=SEED)

    mean_mae = float(np.mean(np.abs(yte - ytr.mean())))
    # Piece count alone is the obvious deterministic rule: bigger set, older
    # audience. Binning by decile and predicting each bin's median is the
    # strongest form of it that needs no model.
    piece_col = X.shape[1] - 3
    edges = np.quantile(Xtr[:, piece_col], np.linspace(0, 1, 11))
    bins_tr = np.clip(np.digitize(Xtr[:, piece_col], edges[1:-1]), 0, 9)
    bins_te = np.clip(np.digitize(Xte[:, piece_col], edges[1:-1]), 0, 9)
    medians = np.array([
        np.median(ytr[bins_tr == b]) if (bins_tr == b).any() else ytr.mean()
        for b in range(10)
    ])
    decile_mae = float(np.mean(np.abs(yte - medians[bins_te])))

    reg = HistGradientBoostingRegressor(random_state=SEED, max_iter=300)
    reg.fit(Xtr, ytr)
    model_mae = float(np.mean(np.abs(yte - reg.predict(Xte))))

    print()
    print(f"  MAE predicting the mean age      : {mean_mae:.2f} years")
    print(f"  MAE from piece-count deciles     : {decile_mae:.2f} years")
    print(f"  MAE from the model               : {model_mae:.2f} years")
    print()

    best_base = min(mean_mae, decile_mae)
    if model_mae < best_base:
        print(f"GATE PASSED: {best_base - model_mae:.2f} years better than the "
              f"best deterministic rule. Ship 6.4.")
        return 0
    print(f"GATE FAILED: {model_mae:.2f} vs {best_base:.2f}. PRD 6 says cut it.")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["fetch", "eval"])
    ap.add_argument("--from", dest="year_from", type=int, default=1990)
    ap.add_argument("--to", dest="year_to", type=int, default=2025)
    args = ap.parse_args(argv)
    if args.command == "fetch":
        return fetch(args.year_from, args.year_to)
    return evaluate()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
