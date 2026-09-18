"""MOCForge - part role taxonomy and inventory capability profiling.

Every archetype so far hand-wrote regex predicates for the parts it needed
("^Brick 1 x 2$", "^Plate Special 2 x 2 with Wheel Holders"). That rigidity was
measured as the direct cause of most generation failures: one over-specific
predicate withheld 5 of 9 inventories, and relaxing a single role to a measured
property ("any part with 2+ wheel pins") doubled coverage.

This module replaces those ad-hoc predicates with one shared vocabulary:

  classify(name, geom) -> Role     what KIND of part this is, plus its measured
                                   footprint, height and connection counts
  profile(lots)        -> Profile  what an inventory can actually BUILD WITH,
                                   aggregated into that vocabulary

Nothing here depends on which archetypes exist, so it is safe to build before
the archetype library is designed.

Usage:
  python roles.py audit              classify the whole catalogue, report coverage
  python roles.py profile 10696-1    capability profile for an inventory
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from inventory import Catalogue
from ldraw import LDU_PER_PLATE, LDU_PER_STUD, Geometry, LDrawLibrary

# Families, most specific first - the first match wins. Ordering matters:
# "Brick Special 2 x 2 with Pin" must be caught before plain "Brick".
FAMILY_RULES: list[tuple[str, str]] = [
    # Duplo first, and deliberately NOT structural. Duplo is double-scale and
    # does not interlock with System, so mixing the two produces a model that
    # cannot be built. It is also the single most common thing in the catalogue
    # that was falling into 'other' (19,311 Duplo Brick 2x2 alone).
    ("duplo",         r"^Duplo\b|^Quatro\b|^Primo\b"),
    ("sticker",       r"^Sticker|^Tape\b"),
    ("minifig",       r"^Minifig|^Torso\b|^Legs\b|^Hair\b|^Headgear|^Hipwear"),
    ("tyre",          r"^Tyre\b"),
    ("wheel",         r"^Wheel\b"),
    ("technic_axle",  r"^Technic Axle\b"),
    ("technic_pin",   r"^Technic Pin\b"),
    ("technic_beam",  r"^Technic Beam\b"),
    ("technic_gear",  r"^Technic Gear\b|^Technic Turntable|^Technic Worm"),
    ("technic_panel", r"^Technic Panel\b"),
    ("technic_other", r"^Technic\b"),
    ("bracket",       r"^Bracket\b"),
    ("hinge",         r"^Hinge\b"),
    ("clip",          r"\bwith Clip"),
    ("slope",         r"^Brick Sloped\b|^Slope\b|^Brick Curved\b"),
    ("wedge",         r"^Wedge\b"),
    ("panel",         r"^Panel\b"),
    ("arch",          r"^Brick Arch\b"),
    ("round_brick",   r"^Brick Round\b|^Cylinder\b|^Cone\b|^Dome\b"),
    ("round_plate",   r"^Plate Round\b|^Dish\b"),
    ("brick_special", r"^Brick Special\b"),
    ("plate_special", r"^Plate Special\b"),
    # Plain rectangular bricks and plates: the safe bulk tiling material.
    # Only ~22 brick and ~35 plate sizes exist with a bare "N x M" name, but
    # they are by far the most numerous pieces in the catalogue - which is why
    # this taxonomy must be judged piece-weighted, never by distinct part count.
    ("brick",         r"^Brick \d+ x \d+$"),
    ("plate",         r"^Plate \d+ x \d+$"),
    ("tile",          r"^Tile \d+ x \d+$|^Tile\b"),
    # Modified variants - a brick with a groove or side studs is still usable
    # structure, just not freely interchangeable. 4,563 names start with
    # "Brick " and only 22 are plain, so without these rules the great majority
    # of real building material was landing in 'other'.
    ("brick_modified", r"^Brick \d+ x \d+"),
    ("plate_modified", r"^Plate \d+ x \d+"),
    ("window",        r"^Window\b|^Door\b|^Windscreen\b|^Glass\b"),
    ("plant",         r"^Plant\b|^Animal\b|^Food\b"),
    ("bar",           r"^Bar \d|^Bar\b"),
    ("baseplate",     r"^Baseplate\b"),
    ("figure_acc",    r"^Weapon\b|^Tool\b|^Utensil\b|^Container\b|^Flag\b"),
    ("electric",      r"^Electric\b|^Power Functions|^Light\b|^Motor\b"),
]
_COMPILED = [(fam, re.compile(rx, re.I)) for fam, rx in FAMILY_RULES]

# Families usable as bulk structural material for walls, decks and sculpture.
# This is the pool a non-vehicle archetype draws on, and 85.4% of sets with
# 100+ parts carry enough of it - far above the 38.8% ceiling for anything
# requiring four wheels.
STRUCTURAL = frozenset({"brick", "plate", "tile", "slope", "wedge", "arch",
                        "round_brick", "round_plate"})


@dataclass
class Role:
    """What a part is, in functional terms, with its measured dimensions."""
    part_num: str
    name: str
    family: str
    width: float          # studs
    depth: float          # studs
    height: float         # plates
    studs: int
    sockets: int
    pin_holes: int
    axle_holes: int
    wheel_pins: int
    rectangular: bool     # integral stud footprint, so safe to tile with

    @property
    def stud_area(self) -> int:
        return int(round(self.width)) * int(round(self.depth))

    @property
    def structural(self) -> bool:
        return self.family in STRUCTURAL and self.rectangular


def classify(part_num: str, name: str, geom: Geometry) -> Role:
    family = "other"
    for fam, rx in _COMPILED:
        if rx.search(name):
            family = fam
            break
    w, h, d = geom.size_studs
    rect = (abs(w - round(w)) <= 0.2 and abs(d - round(d)) <= 0.2
            and round(w) >= 1 and round(d) >= 1)
    return Role(
        part_num=part_num, name=name, family=family,
        width=w, depth=d, height=h,
        studs=geom.count("stud"), sockets=geom.count("socket"),
        pin_holes=geom.count("pin_hole"), axle_holes=geom.count("axle_hole"),
        wheel_pins=geom.count("wheel_pin"), rectangular=rect,
    )


@dataclass
class Profile:
    """What an inventory can build with, in functional terms."""
    set_nums: tuple[str, ...]
    pieces: int
    lots: int
    geometry_pieces: int              # pieces we have geometry for
    by_family: Counter = field(default_factory=Counter)      # pieces
    area_by_family: Counter = field(default_factory=Counter)  # stud area
    roles: dict[tuple[str, int], Role] = field(default_factory=dict)
    quantities: Counter = field(default_factory=Counter)

    # -- capability questions an archetype actually wants to ask ------------

    @property
    def structural_area(self) -> int:
        """Total stud area of plain rectangular building material."""
        return sum(self.area_by_family[f] for f in STRUCTURAL)

    def count(self, *families: str) -> int:
        return sum(self.by_family[f] for f in families)

    def largest_footprint(self, *families: str) -> tuple[float, float]:
        best = (0.0, 0.0)
        for (pn, _cid), r in self.roles.items():
            if families and r.family not in families:
                continue
            if r.width * r.depth > best[0] * best[1]:
                best = (r.width, r.depth)
        return best

    def can_make_wheels(self) -> bool:
        """Four matching wheels, plus something to mount them on."""
        wheels = [r for r in self.roles.values() if r.family == "wheel"]
        if sum(self.quantities[(r.part_num, 0)] for r in wheels) < 0:
            pass
        mounts = any(r.wheel_pins >= 2 or r.axle_holes >= 1
                     for r in self.roles.values())
        return self.by_family["wheel"] >= 4 and mounts

    def summary(self) -> str:
        out = [
            f"inventory   : {' + '.join(self.set_nums)}",
            f"pieces      : {self.pieces:,} in {self.lots} lots "
            f"({self.geometry_pieces:,} with geometry, "
            f"{self.geometry_pieces / max(self.pieces, 1):.1%})",
            f"structural  : {self.structural_area:,} studs of rectangular "
            f"brick/plate/tile area",
            f"wheels      : {self.by_family['wheel']} wheels, "
            f"{self.by_family['tyre']} tyres, "
            f"mountable={'yes' if self.can_make_wheels() else 'no'}",
            "families    :",
        ]
        for fam, n in self.by_family.most_common(12):
            out.append(f"    {fam:<16} {n:>5} pieces  "
                       f"{self.area_by_family[fam]:>6} studs")
        return "\n".join(out)


class Taxonomy:
    def __init__(self, cat: Catalogue | None = None, lib: LDrawLibrary | None = None):
        self.cat = cat or Catalogue()
        self.lib = lib or LDrawLibrary()
        self._names = {p: n for p, n in
                       self.cat.conn.execute("SELECT part_num, name FROM parts")}
        self._ldmap = {p: l for p, l in
                       self.cat.conn.execute("SELECT part_num, ldraw_id FROM ldraw_map")}
        self._cache: dict[str, Role | None] = {}

    def role(self, part_num: str) -> Role | None:
        """Classified role, or None when no geometry is available."""
        if part_num in self._cache:
            return self._cache[part_num]
        name = self._names.get(part_num, "")
        try:
            geom = self.lib.geometry(self._ldmap.get(part_num, part_num))
        except Exception:
            self._cache[part_num] = None
            return None
        r = classify(part_num, name, geom)
        self._cache[part_num] = r
        return r

    def profile(self, *set_nums: str) -> Profile:
        lots = self.cat.combine(*set_nums)
        prof = Profile(set_nums=set_nums, pieces=sum(lots.values()),
                       lots=len(lots), geometry_pieces=0)
        for (part_num, color_id), qty in lots.items():
            r = self.role(part_num)
            if r is None:
                continue
            prof.geometry_pieces += qty
            prof.by_family[r.family] += qty
            if r.rectangular:
                prof.area_by_family[r.family] += r.stud_area * qty
            prof.roles[(part_num, color_id)] = r
            prof.quantities[(part_num, color_id)] += qty
        return prof


# --------------------------------------------------------------------- audit

def audit(limit: int | None = None) -> int:
    """Classify the whole catalogue, reported piece-weighted.

    Distinct-part shares are actively misleading here: there are only 22 plain
    brick names against 4,563 "Brick ..." names overall, yet those 22 account
    for a large share of every real inventory. So the gate is set on how many
    actual PIECES land in 'other', not how many part numbers.
    """
    tax = Taxonomy()
    rows = tax.cat.conn.execute("SELECT part_num, name FROM parts").fetchall()
    if limit:
        rows = rows[:limit]
    occur = Counter(dict(tax.cat.conn.execute(
        """SELECT part_num, SUM(quantity) FROM inventory_parts
           WHERE is_spare = 0 GROUP BY part_num""")))

    fam_parts, fam_pieces, nonrect = Counter(), Counter(), Counter()
    no_geom_parts = no_geom_pieces = 0
    examples: list[tuple[int, str]] = []
    for part_num, name in rows:
        q = occur.get(part_num, 0)
        r = tax.role(part_num)
        if r is None:
            no_geom_parts += 1
            no_geom_pieces += q
            continue
        fam_parts[r.family] += 1
        fam_pieces[r.family] += q
        if not r.rectangular:
            nonrect[r.family] += 1
        if r.family == "other":
            examples.append((q, f"{part_num}: {name[:46]}"))

    total_parts = len(rows)
    total_pieces = sum(fam_pieces.values()) + no_geom_pieces
    classified_pieces = sum(fam_pieces.values())
    print(f"catalogue parts   : {total_parts:,} distinct, "
          f"{total_pieces:,} piece occurrences")
    print(f"  with geometry   : {sum(fam_parts.values()):,} parts / "
          f"{classified_pieces:,} pieces ({classified_pieces/total_pieces:.1%})")
    print(f"  without geometry: {no_geom_parts:,} parts / "
          f"{no_geom_pieces:,} pieces\n")
    print(f"{'family':<17}{'parts':>8}{'pieces':>11}{'piece share':>13}"
          f"{'non-rect':>10}")
    for f, n in fam_pieces.most_common():
        print(f"    {f:<16} {fam_parts[f]:>6,} {n:>10,}   "
              f"{n/classified_pieces:>9.1%}   {nonrect[f]:>7,}")

    struct = sum(fam_pieces[f] for f in STRUCTURAL)
    print(f"\nstructural families total: {struct:,} pieces "
          f"({struct/classified_pieces:.1%}) - the pool non-vehicle "
          f"archetypes draw on")

    if examples:
        examples.sort(reverse=True)
        print("\nmost common unclassified parts:")
        for q, e in examples[:8]:
            print(f"    {q:>7,} x  {e}")

    share = fam_pieces["other"] / max(classified_pieces, 1)
    print()
    if share > 0.20:
        print(f"GATE FAILED: {share:.1%} of PIECES fall into 'other'; "
              f"the taxonomy needs more rules (limit 20%).")
        return 1
    print(f"GATE PASSED: only {share:.1%} of pieces unclassified (limit 20%).")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if cmd == "audit":
        sys.exit(audit(int(sys.argv[2]) if len(sys.argv) > 2 else None))
    elif cmd == "profile":
        tax = Taxonomy()
        print(tax.profile(*sys.argv[2:]).summary())
    else:
        raise SystemExit(__doc__)
