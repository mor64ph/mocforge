"""Survey what archetypes the catalogue could support, using the role taxonomy.

Answers the sizing question for the coverage fix: how many sets have enough of
each KIND of material, independent of any archetype existing yet.
"""
import random, sys, time, collections
from roles import Taxonomy, STRUCTURAL

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
MIN_PARTS = int(sys.argv[2]) if len(sys.argv) > 2 else 100

tax = Taxonomy()
rows = [r[0] for r in tax.cat.conn.execute(
    "SELECT set_num FROM sets WHERE num_parts >= ? ORDER BY set_num", (MIN_PARTS,))]
random.seed(42)
sample = random.sample(rows, min(N, len(rows)))

# Capability tests, each the precondition for a class of archetype.
TESTS = {
    "vehicle (4 wheels + mount)":   lambda p: p.can_make_wheels(),
    "wall/building (>=300 struct studs)": lambda p: p.structural_area >= 300,
    "small build (>=120 struct studs)":   lambda p: p.structural_area >= 120,
    "tower (>=60 bricks)":          lambda p: p.count("brick", "brick_modified") >= 60,
    "roofed (>=8 slopes)":          lambda p: p.count("slope") >= 8,
    "mosaic (>=100 plate/tile)":    lambda p: p.count("plate", "tile", "round_plate") >= 100,
    "technic frame (>=2 beams)":    lambda p: p.count("technic_beam") >= 2,
}
hits = collections.Counter()
areas = []
t0 = time.time()
for i, sn in enumerate(sample, 1):
    try:
        p = tax.profile(sn)
    except Exception:
        continue
    areas.append(p.structural_area)
    for name, fn in TESTS.items():
        try:
            if fn(p):
                hits[name] += 1
        except Exception:
            pass
    if i % 100 == 0:
        print(f"  {i}/{len(sample)}  {time.time()-t0:.0f}s")

k = len(areas)
print(f"\nsample: {k} sets with >= {MIN_PARTS} parts (seed 42)\n")
print(f"{'archetype precondition':<38}{'sets':>7}{'share':>9}")
for name, _fn in sorted(TESTS.items(), key=lambda kv: -hits[kv[0]]):
    print(f"  {name:<36} {hits[name]:>6} {hits[name]/k:>8.1%}")
areas.sort()
print(f"\nstructural stud area: median {areas[k//2]:,}  "
      f"p25 {areas[k//4]:,}  p75 {areas[3*k//4]:,}  max {areas[-1]:,}")
union = sum(1 for sn in sample[:0])  # placeholder
print("\n=> the 'small build' precondition alone reaches "
      f"{hits['small build (>=120 struct studs)']/k:.1%}, versus "
      f"{hits['vehicle (4 wheels + mount)']/k:.1%} for vehicles.")
