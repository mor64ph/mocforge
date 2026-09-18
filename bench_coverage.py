"""Measure what fraction of the real catalogue the generator can serve."""
import random, sqlite3, sys, time, collections
from inventory import Catalogue
from ldraw import LDrawLibrary
from generate import Fitter, TEMPLATES

N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
MIN_PARTS = int(sys.argv[2]) if len(sys.argv) > 2 else 100

cat, lib = Catalogue(), LDrawLibrary()
fitter = Fitter(cat, lib)

# Total catalogue size, and how much of it is even usable.
tot_sets = cat.conn.execute("SELECT COUNT(*) FROM sets").fetchone()[0]
with_inv = cat.conn.execute(
    """SELECT COUNT(DISTINCT s.set_num) FROM sets s
       JOIN inventories i ON i.set_num = s.set_num""").fetchone()[0]
with_parts = cat.conn.execute(
    """SELECT COUNT(*) FROM (SELECT s.set_num FROM sets s
       JOIN inventories i ON i.set_num=s.set_num
       JOIN inventory_parts ip ON ip.inventory_id=i.id AND ip.is_spare=0
       GROUP BY s.set_num)""").fetchone()[0]
bands = cat.conn.execute(
    """SELECT CASE WHEN num_parts < 25 THEN 'a <25'
                   WHEN num_parts < 100 THEN 'b 25-99'
                   WHEN num_parts < 300 THEN 'c 100-299'
                   WHEN num_parts < 1000 THEN 'd 300-999'
                   ELSE 'e 1000+' END band, COUNT(*)
       FROM sets WHERE num_parts IS NOT NULL GROUP BY band ORDER BY band""").fetchall()

print(f"catalogue: {tot_sets:,} sets   with an inventory: {with_inv:,}   "
      f"with actual parts: {with_parts:,}")
for b, n in bands:
    print(f"    {b:<10} {n:>6,}")
eligible = cat.conn.execute(
    "SELECT COUNT(*) FROM sets WHERE num_parts >= ?", (MIN_PARTS,)).fetchone()[0]
print(f"    sets with >= {MIN_PARTS} parts: {eligible:,}\n")

rows = cat.conn.execute(
    "SELECT set_num FROM sets WHERE num_parts >= ? ORDER BY set_num", (MIN_PARTS,)
).fetchall()
random.seed(42)
sample = random.sample([r[0] for r in rows], min(N, len(rows)))

t0 = time.time()
served = 0
offers_hist = collections.Counter()
by_tpl = collections.Counter()
reasons = collections.Counter()
for i, sn in enumerate(sample, 1):
    try:
        lots = cat.set_lots(sn)
    except Exception:
        continue
    n = 0
    for name, tpl in TEMPLATES.items():
        for variant in tpl.variants:
            b = fitter.fit(tpl, lots, variant)
            vn = name + (("-" + variant["name"]) if "name" in variant else "")
            if b.buildable:
                n += 1
                by_tpl[vn] += 1
                continue
            why = (b.unfilled or b.collisions or b.unsupported
                   or ["too few pieces"])
            reasons[(why[0].split("(")[0].strip() if why else "?")[:44]] += 1
    offers_hist[n] += 1
    if n:
        served += 1
    if i % 100 == 0:
        print(f"  {i}/{len(sample)}  served {served} ({served/i:.1%})  "
              f"{time.time()-t0:.0f}s")

k = len(sample)
print(f"\nsample: {k} sets with >= {MIN_PARTS} parts, seed 42, {time.time()-t0:.0f}s")
print(f"served (>=1 buildable MOC): {served}/{k} = {served/k:.1%}")
print("\noffers per set:")
for n in sorted(offers_hist):
    print(f"    {n} design(s): {offers_hist[n]:>4}  ({offers_hist[n]/k:5.1%})")
print("\nbuildable by archetype:")
for t, n in by_tpl.most_common():
    print(f"    {t:16s} {n:>4}  ({n/k:5.1%} of sampled sets)")
print("\ntop withholding reasons:")
for r, n in reasons.most_common(8):
    print(f"    {n:>5}  {r}")
