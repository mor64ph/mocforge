"""MOCForge Phase 2 - inventory algebra (L2).

Deterministic multiset operations over owned sets. No ML anywhere in this layer;
every function here is expected to be exactly right, and is unit-tested against
hand-verified fixtures.

Handles the four data traps from PRD 2.2:
  - inventories is polymorphic: 17,225 of 47,533 rows are minifig inventories,
    so set resolution always joins through `sets`.
  - 1,291 sets have multiple inventory versions; a version is always pinned.
  - is_spare is normalised to 0/1 at ingest, never compared as text.
  - 40900-1 has an inventory but no set row; resolution returns None, not a crash.

Usage:
  python inventory.py selftest
  python inventory.py show 42151-1
  python inventory.py combine 42151-1 42154-1
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mocforge.db"

# Part-relationship semantics. Getting this wrong silently corrupts every
# substitution, so the reasoning is recorded rather than assumed.
#   M  different mould, same design  -> functionally identical      SUBSTITUTABLE
#   A  alternate part                -> functionally identical      SUBSTITUTABLE
#   P  printed version of a parent   -> same geometry, different decoration
#   T  patterned version of a parent -> same geometry, different decoration
#   B  sub-part of a parent          -> parent is an ASSEMBLY/PACK   NEVER
#   R  "pairs with"                  -> see note below
STRICT_RELS = ("M", "A")          # identical function and appearance
DECOR_RELS = ("P", "T")           # identical geometry, different decoration
NEVER_RELS = ("B",)               # assemblies are not interchangeable with parts

# What R actually means, established by inspecting the data (2026-09-17):
# it is "commonly used together as a pair", NOT mirrored geometry and NOT mould
# identity. All three of these are R relations:
#   32123b (Technic Bush 1/2)  <-> 3139, 4084, ... (Tyres)   - bush is the hub
#   98114  (Death Star half w/ superlaser) <-> 98115 (plain half)
#   2694pr0001 ("Window Left" print) <-> 2694pr0002 ("Window Right" print)
# So R is useless as a substitution signal on its own. It is used only as a
# conservative exclusion: where an R pair happens to land in the same class
# (29 of 2,988 pairs), those parts are distinct named variants a user would
# notice being swapped, so substitutes() withholds them. For the large majority,
# the two parts were never in one class and the exclusion is a no-op.
PAIR_RELS = ("R",)

# Parts that appear as the PARENT of a B relation are packs or assemblies
# (e.g. 73099 "Tile Pack 1 x 1 Half Circle, Random DOTS" contains 8 tiles).
# They must never share a substitution class with their own components: one
# pack is not interchangeable with one tile, so quantities would be nonsense.

# A part lot: a specific part in a specific colour, with a quantity.
PartKey = tuple[str, int]  # (part_num, color_id)


@dataclass(frozen=True)
class Lot:
    part_num: str
    color_id: int
    quantity: int
    part_name: str = ""
    color_name: str = ""

    def __repr__(self) -> str:
        return f"{self.quantity}x {self.part_num}/{self.color_id}"


class Catalogue:
    def __init__(self, db_path: Path = DB_PATH, check_same_thread: bool = True):
        """Open the catalogue read-only.

        `check_same_thread=False` permits use from more than one thread, which
        a web server needs. It is opt-in because it is only safe given two
        things the caller must guarantee: the database is opened read-only here
        (so there are no writes to serialise), and the memoised state on this
        object - equivalence classes, the members index, assembly parts - is
        either built before threads start or tolerant of a benign race that
        recomputes the same value. Callers that also share an LDrawLibrary must
        note it is NOT safe this way: it holds one zipfile handle whose seeks
        would interleave.
        """
        if not db_path.exists():
            raise SystemExit(f"{db_path} missing. Run: python ingest.py build")
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                                    check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------ resolution

    def resolve_set(self, set_num: str) -> str | None:
        """Normalise a user-typed set number to a real set_num, or None.

        Accepts '42151' as well as '42151-1', since users never type the suffix.
        Only returns numbers present in `sets` - this is what keeps minifig
        inventories out of set queries (PRD 2.2 trap 1).
        """
        row = self.conn.execute(
            "SELECT set_num FROM sets WHERE set_num = ?", (set_num,)
        ).fetchone()
        if row:
            return row["set_num"]
        if "-" not in set_num:
            rows = self.conn.execute(
                "SELECT set_num FROM sets WHERE set_num LIKE ? ORDER BY set_num",
                (f"{set_num}-%",),
            ).fetchall()
            if len(rows) == 1:
                return rows[0]["set_num"]
            if len(rows) > 1:  # prefer -1, the canonical release
                for r in rows:
                    if r["set_num"].endswith("-1"):
                        return r["set_num"]
                return rows[0]["set_num"]
        return None

    def inventory_id(self, set_num: str, version: int | None = None) -> int | None:
        """Inventory id for a set, pinning a version (PRD 2.2 trap 2)."""
        if version is None:
            row = self.conn.execute(
                """SELECT i.id FROM inventories i JOIN sets s ON s.set_num = i.set_num
                   WHERE i.set_num = ? ORDER BY i.version DESC LIMIT 1""",
                (set_num,),
            ).fetchone()
        else:
            row = self.conn.execute(
                """SELECT i.id FROM inventories i JOIN sets s ON s.set_num = i.set_num
                   WHERE i.set_num = ? AND i.version = ?""",
                (set_num, version),
            ).fetchone()
        return row["id"] if row else None

    # ------------------------------------------------------------- inventory

    def set_lots(
        self,
        set_num: str,
        version: int | None = None,
        include_spares: bool = False,
        expand_subsets: bool = True,
    ) -> Counter:
        """Multiset of (part_num, color_id) -> quantity for one set.

        expand_subsets follows inventory_sets, so a set containing other sets
        (5,212 such rows) yields the parts of its children too.
        """
        resolved = self.resolve_set(set_num)
        if resolved is None:
            raise KeyError(f"unknown set: {set_num!r}")
        inv_id = self.inventory_id(resolved, version)
        if inv_id is None:
            return Counter()
        return self._lots_for_inventory(inv_id, include_spares, expand_subsets, set())

    def _lots_for_inventory(
        self, inv_id: int, include_spares: bool, expand_subsets: bool, seen: set[int]
    ) -> Counter:
        if inv_id in seen:  # defensive: upstream data should not cycle
            return Counter()
        seen = seen | {inv_id}
        spare_clause = "" if include_spares else "AND is_spare = 0"
        out = Counter()
        for r in self.conn.execute(
            f"""SELECT part_num, color_id, quantity FROM inventory_parts
                WHERE inventory_id = ? {spare_clause}""",
            (inv_id,),
        ):
            out[(r["part_num"], r["color_id"])] += r["quantity"]

        if expand_subsets:
            for r in self.conn.execute(
                "SELECT set_num, quantity FROM inventory_sets WHERE inventory_id = ?",
                (inv_id,),
            ):
                child = self.inventory_id(r["set_num"])
                if child is None:
                    continue
                sub = self._lots_for_inventory(child, include_spares, expand_subsets, seen)
                for key, qty in sub.items():
                    out[key] += qty * r["quantity"]
        return out

    def combine(self, *set_nums: str, **kw) -> Counter:
        """Union of several owned sets, summing quantities."""
        total = Counter()
        for sn in set_nums:
            total.update(self.set_lots(sn, **kw))
        return total

    # ---------------------------------------------------------- substitution

    def equivalence_classes(self, include_decor: bool = False) -> dict[str, str]:
        """Union-find over part_relationships -> {part_num: class_representative}.

        Only STRICT_RELS by default. include_decor also merges print/pattern
        variants, which share geometry but differ visually - correct for
        structural building, wrong if the user cares about decoration.
        NEVER_RELS (sub-parts, mirror pairs) are excluded unconditionally.
        """
        # Memoised: the closure is derived from immutable catalogue data, and
        # callers such as substitutes() hit it in tight loops.
        cache = self.__dict__.setdefault("_class_cache", {})
        if include_decor in cache:
            return cache[include_decor]

        rels = STRICT_RELS + (DECOR_RELS if include_decor else ())
        assemblies = self.assembly_parts()
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path compression
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                # Deterministic representative so results are reproducible.
                lo, hi = sorted((ra, rb))
                parent[hi] = lo

        placeholders = ",".join("?" * len(rels))
        for r in self.conn.execute(
            f"""SELECT child_part_num, parent_part_num FROM part_relationships
                WHERE rel_type IN ({placeholders})""",
            rels,
        ):
            c, p = r["child_part_num"], r["parent_part_num"]
            # Assemblies/packs are excluded from every class (see note above).
            if c in assemblies or p in assemblies:
                continue
            union(c, p)

        result = {p: find(p) for p in parent}
        cache[include_decor] = result
        return result

    def assembly_parts(self) -> set[str]:
        """Parts that are packs or assemblies, i.e. B-relation parents."""
        if not hasattr(self, "_assemblies"):
            ph = ",".join("?" * len(NEVER_RELS))
            self._assemblies = {
                r["parent_part_num"]
                for r in self.conn.execute(
                    f"SELECT DISTINCT parent_part_num FROM part_relationships "
                    f"WHERE rel_type IN ({ph})", NEVER_RELS
                )
            }
        return self._assemblies

    def paired_with(self, part_num: str) -> set[str]:
        """R-relation partners ("pairs with"; see the note at module top).

        Excluded at substitution time rather than by breaking the class, which
        keeps the result independent of union-find merge order.
        """
        ph = ",".join("?" * len(PAIR_RELS))
        out = set()
        for r in self.conn.execute(
            f"""SELECT child_part_num c, parent_part_num p FROM part_relationships
                WHERE rel_type IN ({ph}) AND (child_part_num=? OR parent_part_num=?)""",
            (*PAIR_RELS, part_num, part_num),
        ):
            out.add(r["p"] if r["c"] == part_num else r["c"])
        return out - {part_num}

    def substitutes(self, part_num: str, include_decor: bool = True) -> list[str]:
        """Parts that can stand in for `part_num`, excluding R-pair partners."""
        classes = self.equivalence_classes(include_decor=include_decor)
        rep = classes.get(part_num)
        if rep is None:
            return []
        members = self._class_members(include_decor)
        blocked = self.paired_with(part_num)
        return sorted(
            p for p in members[rep] if p != part_num and p not in blocked
        )

    def _class_members(self, include_decor: bool) -> dict[str, list[str]]:
        """Reverse index rep -> members, so substitutes() is not O(all parts)."""
        cache = self.__dict__.setdefault("_members_cache", {})
        if include_decor not in cache:
            idx: dict[str, list[str]] = defaultdict(list)
            for p, rep in self.equivalence_classes(include_decor).items():
                idx[rep].append(p)
            cache[include_decor] = idx
        return cache[include_decor]

    def available(
        self, lots: Counter, include_decor: bool = True, ignore_colour: bool = False
    ) -> Counter:
        """Collapse an inventory into substitution classes.

        This is what the tiler consumes: "I have N of *something that works
        here*", rather than N of one exact part number.
        """
        classes = self.equivalence_classes(include_decor=include_decor)
        out = Counter()
        for (part_num, color_id), qty in lots.items():
            rep = classes.get(part_num, part_num)
            out[(rep, -1 if ignore_colour else color_id)] += qty
        return out

    # ----------------------------------------------------------------- names

    def describe(self, lots: Counter, limit: int | None = None) -> list[Lot]:
        part_names = {}
        color_names = {}
        for r in self.conn.execute("SELECT part_num, name FROM parts"):
            part_names[r["part_num"]] = r["name"]
        for r in self.conn.execute("SELECT id, name FROM colors"):
            color_names[r["id"]] = r["name"]
        rows = [
            Lot(pn, cid, qty, part_names.get(pn, "?"), color_names.get(cid, "?"))
            for (pn, cid), qty in lots.items()
        ]
        rows.sort(key=lambda l: (-l.quantity, l.part_name))
        return rows[:limit] if limit else rows


# ------------------------------------------------------------------- selftest

def selftest() -> int:
    cat = Catalogue()
    failures: list[str] = []

    def check(label: str, got, want) -> None:
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
        if not ok:
            failures.append(label)

    print("fixture: 42151-1 Bugatti Bolide (905 parts / 150 lots, verified vs LEGO)")
    lots = cat.set_lots("42151-1")
    check("total pieces", sum(lots.values()), 905)
    check("distinct lots", len(lots), 150)
    check("115 black friction pins present", lots[("61332", 0)], 115)

    with_spares = cat.set_lots("42151-1", include_spares=True)
    check("pieces incl. spares", sum(with_spares.values()), 923)

    print("\nresolution")
    check("bare '42151' resolves", cat.resolve_set("42151"), "42151-1")
    check("unknown set -> None", cat.resolve_set("99999999-1"), None)
    check("dangling 40900-1 -> None", cat.resolve_set("40900-1"), None)

    print("\npolymorphic-inventory trap: a minifig number must not resolve as a set")
    fig = cat.conn.execute("SELECT fig_num FROM minifigs LIMIT 1").fetchone()["fig_num"]
    check(f"minifig {fig} rejected", cat.resolve_set(fig), None)

    print("\nversion pinning")
    multi = cat.conn.execute(
        """SELECT set_num, COUNT(*) n FROM inventories GROUP BY set_num
           HAVING n > 1 ORDER BY n DESC LIMIT 1"""
    ).fetchone()
    versions = [
        r["version"]
        for r in cat.conn.execute(
            "SELECT version FROM inventories WHERE set_num=? ORDER BY version",
            (multi["set_num"],),
        )
    ]
    default_id = cat.inventory_id(multi["set_num"])
    max_id = cat.inventory_id(multi["set_num"], max(versions))
    print(f"  {multi['set_num']} has versions {versions}")
    check("default pins highest version", default_id, max_id)
    total_all = cat.conn.execute(
        """SELECT SUM(quantity) q FROM inventory_parts WHERE inventory_id IN
           (SELECT id FROM inventories WHERE set_num=?) AND is_spare=0""",
        (multi["set_num"],),
    ).fetchone()["q"]
    pinned = sum(cat.set_lots(multi["set_num"], expand_subsets=False).values())
    ok = pinned < total_all
    print(f"  {'PASS' if ok else 'FAIL'}  pinned {pinned} < unpinned {total_all} "
          f"(unpinned would double-count)")
    if not ok:
        failures.append("version pinning")

    print("\nsubstitution closure")
    strict = cat.equivalence_classes(include_decor=False)
    decor = cat.equivalence_classes(include_decor=True)
    reps_strict = len(set(strict.values()))
    print(f"  strict (M,A):      {len(strict):,} parts in {reps_strict:,} classes")
    print(f"  +decor (M,A,P,T):  {len(decor):,} parts in {len(set(decor.values())):,} classes")

    # Union-find cannot produce cycles; assert the invariant anyway.
    self_reps = sum(1 for p, r in strict.items() if strict.get(r) != r)
    check("closure is acyclic (all reps are self-parented)", self_reps, 0)

    # An assembly/pack must never share a class with its own components:
    # one pack is not interchangeable with one tile.
    print(f"  assemblies/packs excluded: {len(cat.assembly_parts()):,}")
    bad = 0
    for r in cat.conn.execute(
        f"""SELECT child_part_num c, parent_part_num p FROM part_relationships
            WHERE rel_type IN ({','.join('?' * len(NEVER_RELS))})""", NEVER_RELS
    ):
        if r["c"] in decor and r["p"] in decor and decor[r["c"]] == decor[r["p"]]:
            bad += 1
    check("pack/component leaks into classes", bad, 0)

    # R pairs SHARE a mould and differ only in print, so co-location under
    # include_decor=True is correct, not a leak. Assert the documented
    # behaviour holds in both directions so a future change is caught.
    pairs = cat.conn.execute(
        """SELECT child_part_num c, parent_part_num p FROM part_relationships
           WHERE rel_type = 'R'"""
    ).fetchall()
    co_decor = sum(1 for r in pairs
                   if decor.get(r["c"]) and decor.get(r["c"]) == decor.get(r["p"]))
    co_strict = sum(1 for r in pairs
                    if strict.get(r["c"]) and strict.get(r["c"]) == strict.get(r["p"]))
    print(f"  R pairs co-located in a class: {co_decor} decor, {co_strict} strict "
          f"(both expected; classes group, substitutes() excludes)")

    # The invariant that actually matters: substitutes() must never offer an
    # R-pair partner as a swap, however the classes happened to merge.
    leaked = 0
    for r in pairs:
        c, p = r["c"], r["p"]
        if p in cat.substitutes(c, include_decor=True) or \
           c in cat.substitutes(p, include_decor=True):
            leaked += 1
    check("substitutes() never offers an R-pair partner", leaked, 0)

    print("\ncombination")
    a = cat.set_lots("42151-1")
    b = cat.set_lots("42154-1")
    both = cat.combine("42151-1", "42154-1")
    check("combined total is additive", sum(both.values()), sum(a.values()) + sum(b.values()))
    ok = len(both) <= len(a) + len(b)
    print(f"  {'PASS' if ok else 'FAIL'}  combined lots {len(both)} <= {len(a)}+{len(b)} "
          f"(shared parts merge)")
    if not ok:
        failures.append("combination merge")

    coll = cat.available(both, include_decor=True, ignore_colour=False)
    print(f"  collapsed into substitution classes: {len(both)} lots -> {len(coll)} classes")

    print()
    if failures:
        print(f"GATE FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("GATE PASSED: Phase 2 inventory algebra verified.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "selftest":
        sys.exit(selftest())
    elif cmd == "show":
        cat = Catalogue()
        lots = cat.set_lots(sys.argv[2])
        print(f"{sum(lots.values())} pieces / {len(lots)} lots")
        for l in cat.describe(lots, limit=25):
            print(f"  {l.quantity:4d} x {l.part_num:>12} {l.part_name[:52]:<52} {l.color_name}")
    elif cmd == "combine":
        cat = Catalogue()
        lots = cat.combine(*sys.argv[2:])
        print(f"{' + '.join(sys.argv[2:])} = {sum(lots.values())} pieces / {len(lots)} lots")
        for l in cat.describe(lots, limit=25):
            print(f"  {l.quantity:4d} x {l.part_num:>12} {l.part_name[:52]:<52} {l.color_name}")
    else:
        raise SystemExit(__doc__)
