"""MOCForge Phase 3 - LDraw geometry (L1, geometry half).

Resolves LDraw .dat part files into the two things the generator needs:

  1. a bounding box in LDU, measured from real mesh vertices
  2. connection points - studs, Technic pin holes, axle holes - located by
     finding references to LDraw's named connection primitives and recording
     where the composed transform puts them

LDraw conventions:
  20 LDU = 1 stud pitch;  8 LDU = 1 plate height;  24 LDU = 1 brick height
  +Y points DOWN, so a brick's studs are at NEGATIVE Y relative to its base.

Line types used here:
  1 <colour> x y z a b c d e f g h i <file>   sub-file reference, matrix
              | a b c |         | x |
        M  =  | d e f |    T =  | y |
              | g h i |         | z |
  3 / 4                                        triangle / quad vertices

Licensing: the LDraw Parts Library is CC BY 2.0 (newer parts CC BY 4.0 / CC0)
and requires attribution in any shipped product. See PRD "Attribution".

Usage:
  python ldraw.py selftest
  python ldraw.py show 3001
"""

from __future__ import annotations

import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ZIP_PATH = ROOT / "data" / "ldraw" / "complete.zip"

LDU_PER_STUD = 20.0
LDU_PER_PLATE = 8.0
LDU_PER_BRICK = 24.0

# Connection primitives, matched against the referenced filename stem.
# Order matters: the first pattern to match wins, so specific before generic.
#
# The stud/socket split is not a guess - LDraw's own primitive titles state it:
#   stud.dat   "Stud"                 -> male, on top
#   stud2.dat  "Stud Open"            -> male, open-topped
#   stud3.dat  "Stud Tube Solid"      -> FEMALE, underside socket
#   stud4.dat  "Stud Tube Open"       -> FEMALE, underside socket
#   2-4stud4   "Stud Tube Open 0.5"   -> FEMALE, partial socket
# Counting sockets as studs inflates a Brick 2x4 from 8 studs to 11.
CONNECTION_PATTERNS: list[tuple[str, str]] = [
    ("axle_hole", r"^(axlehole|axl[234]hole|axlehol\d|axleho\d+)$"),
    ("pin_hole",  r"^(peghole|npeghole|beamhole|technicpinhole)$"),
    ("socket",    r"^(\d+-\d+)?stud(3|4)[0-9a-z]*$|^studtube"),
    ("stud",      r"^(\d+-\d+)?stud(|2)[0-9a-z]*$"),
    ("axle",      r"^(axle|axleend\d*|axlesphe|axlebeam)$"),
    ("wheel_pin", r"^wpin\d*[a-z]*$"),
    ("clip",      r"^(clikitshole|clikitsstud)$"),
]
_COMPILED = [(k, re.compile(p)) for k, p in CONNECTION_PATTERNS]

# Two connection references within this many LDU are the same feature. A stud
# pitch is 20 LDU, so 4 absorbs modelling jitter without merging neighbours.
CLUSTER_TOL = 4.0

# Holes are frequently modelled as two rim primitives on opposite faces of the
# part - 32524 places its 7th hole as two pegholes at Y=-10 and Y=+10. Those lie
# on a common axis line, so hole features are additionally merged when the
# displacement between them is parallel to their shared axis. This is applied
# only to hole kinds: doing it for studs would wrongly merge a top stud with a
# bottom stud on a double-sided part.
COLLINEAR_KINDS = {"pin_hole", "axle_hole"}


def classify(stem: str) -> str | None:
    stem = stem.lower()
    for kind, rx in _COMPILED:
        if rx.match(stem):
            return kind
    return None


@dataclass
class Connection:
    kind: str
    x: float
    y: float
    z: float
    axis: tuple[float, float, float] = (0.0, 1.0, 0.0)

    def studs(self) -> tuple[float, float, float]:
        """Position in stud/plate units rather than raw LDU."""
        return (self.x / LDU_PER_STUD, self.y / LDU_PER_PLATE, self.z / LDU_PER_STUD)


@dataclass
class Geometry:
    part: str
    min_xyz: tuple[float, float, float]
    max_xyz: tuple[float, float, float]
    connections: list[Connection] = field(default_factory=list)
    vertex_count: int = 0
    unresolved: list[str] = field(default_factory=list)

    @property
    def size_ldu(self) -> tuple[float, float, float]:
        return tuple(round(hi - lo, 3) for lo, hi in zip(self.min_xyz, self.max_xyz))

    @property
    def size_studs(self) -> tuple[float, float, float]:
        w, h, d = self.size_ldu
        return (round(w / LDU_PER_STUD, 3),
                round(h / LDU_PER_PLATE, 3),
                round(d / LDU_PER_STUD, 3))

    def count(self, kind: str) -> int:
        return sum(1 for c in self.connections if c.kind == kind)

    def summary(self) -> str:
        w, h, d = self.size_studs
        kinds = {}
        for c in self.connections:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
        conn = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())) or "none"
        return (f"{self.part}: {w:g} x {d:g} studs, {h:g} plates tall "
                f"({'x'.join(f'{v:g}' for v in self.size_ldu)} LDU) | {conn}")


class LDrawLibrary:
    """Reads parts straight out of complete.zip; nothing is extracted to disk."""

    def __init__(self, zip_path: Path = ZIP_PATH):
        if not zip_path.exists():
            raise SystemExit(f"{zip_path} missing. Download the LDraw library first.")
        self.zip = zipfile.ZipFile(zip_path)
        # Index by lowercase basename so 's\3001s01.dat' and 'p/48/...' resolve.
        self._index: dict[str, str] = {}
        for name in self.zip.namelist():
            if name.lower().endswith(".dat"):
                self._index.setdefault(name.split("/")[-1].lower(), name)
                # Also index by the trailing two segments, to disambiguate
                # same-named files in p/ vs p/48/ (hi-res primitives).
                parts = name.lower().split("/")
                if len(parts) >= 2:
                    self._index.setdefault("/".join(parts[-2:]), name)
        self._text_cache: dict[str, list[str]] = {}

    def _resolve(self, ref: str) -> str | None:
        ref = ref.replace("\\", "/").lower().strip()
        if ref in self._index:
            return self._index[ref]
        base = ref.split("/")[-1]
        return self._index.get(base)

    def _lines(self, member: str) -> list[str]:
        if member not in self._text_cache:
            self._text_cache[member] = (
                self.zip.read(member).decode("latin-1").splitlines()
            )
        return self._text_cache[member]

    def has(self, part_num: str) -> bool:
        return self._resolve(f"{part_num}.dat") is not None

    # ------------------------------------------------------------------ parse

    def geometry(self, part_num: str, max_depth: int = 24) -> Geometry:
        """Recursively resolve a part into bbox + connection points.

        Memoised: part geometry is immutable, and a catalogue-wide sweep hits
        the same common parts (pins, plates, beams) thousands of times over.
        """
        cache = self.__dict__.setdefault("_geom_cache", {})
        if part_num in cache:
            hit = cache[part_num]
            if isinstance(hit, Exception):
                raise hit
            return hit
        try:
            g = self._geometry_uncached(part_num, max_depth)
        except (KeyError, ValueError) as e:
            cache[part_num] = e
            raise
        cache[part_num] = g
        return g

    def _geometry_uncached(self, part_num: str, max_depth: int = 24) -> Geometry:
        member = self._resolve(f"{part_num}.dat")
        if member is None:
            raise KeyError(f"no LDraw file for part {part_num!r}")

        verts: list[tuple[float, float, float]] = []
        # Meshes belonging to connection primitives are kept apart. For an
        # ordinary brick they are excluded from the bounding box on purpose:
        # studs protrude 4 LDU and counting them would cause false collisions
        # with whatever sits above. But some parts ARE nothing but connection
        # primitives - a Technic axle is made of axle.dat - and excluding them
        # collapses the box to a line (3737 measured 200 x 0 x 0). So they are
        # folded back in only when the primary geometry is degenerate.
        conn_verts: list[tuple[float, float, float]] = []
        conns: list[Connection] = []
        unresolved: list[str] = []

        def walk(mem: str, m: tuple, t: tuple, depth: int, seen: frozenset,
                 sink: list | None = None) -> None:
            if depth > max_depth or mem in seen:
                return
            seen = seen | {mem}
            a, b, c, d, e, f, g, h, i = m
            tx, ty, tz = t

            def xform(p):
                x, y, z = p
                return (a * x + b * y + c * z + tx,
                        d * x + e * y + f * z + ty,
                        g * x + h * y + i * z + tz)

            for line in self._lines(mem):
                line = line.strip()
                if not line:
                    continue
                tok = line.split()
                try:
                    kind = int(tok[0])
                except (ValueError, IndexError):
                    continue

                if kind == 1 and len(tok) >= 15:
                    nums = [float(v) for v in tok[2:14]]
                    sx, sy, sz = nums[0:3]
                    sm = tuple(nums[3:12])
                    ref = " ".join(tok[14:])
                    child = self._resolve(ref)
                    if child is None:
                        unresolved.append(ref)
                        continue
                    # Compose parent transform with the child's.
                    cm = (
                        a * sm[0] + b * sm[3] + c * sm[6],
                        a * sm[1] + b * sm[4] + c * sm[7],
                        a * sm[2] + b * sm[5] + c * sm[8],
                        d * sm[0] + e * sm[3] + f * sm[6],
                        d * sm[1] + e * sm[4] + f * sm[7],
                        d * sm[2] + e * sm[5] + f * sm[8],
                        g * sm[0] + h * sm[3] + i * sm[6],
                        g * sm[1] + h * sm[4] + i * sm[7],
                        g * sm[2] + h * sm[5] + i * sm[8],
                    )
                    ct = xform((sx, sy, sz))

                    stem = child.split("/")[-1][:-4]
                    ckind = classify(stem)
                    if ckind:
                        # A connection primitive: record where it sits and which
                        # way it faces. Do not descend - its internal mesh adds
                        # nothing but noise. The primitive's local +Y is its
                        # axis, so column 2 of the composed matrix gives the
                        # axis in part space.
                        axis = (cm[1], cm[4], cm[7])
                        conns.append(Connection(ckind, *ct, axis=axis))
                        (sink if sink is not None else verts).append(ct)
                        # Descend, but route the mesh to the side list.
                        walk(child, cm, ct, depth + 1, seen, conn_verts)
                        continue
                    walk(child, cm, ct, depth + 1, seen, sink)

                elif kind in (3, 4):
                    n = 3 if kind == 3 else 4
                    vals = [float(v) for v in tok[2:2 + n * 3]]
                    target = sink if sink is not None else verts
                    for k in range(n):
                        target.append(xform(tuple(vals[k * 3:k * 3 + 3])))

        identity = (1, 0, 0, 0, 1, 0, 0, 0, 1)
        walk(member, identity, (0, 0, 0), 0, frozenset())

        if not verts and not conn_verts:
            raise ValueError(f"part {part_num!r} produced no geometry")

        def extents(vs):
            return (tuple(min(v[k] for v in vs) for k in range(3)),
                    tuple(max(v[k] for v in vs) for k in range(3)))

        if verts:
            mins, maxs = extents(verts)
            # Degenerate in any axis means the real body lives in the
            # connection primitives (an axle, a pin), so include them.
            if any(maxs[k] - mins[k] < 4.0 for k in range(3)) and conn_verts:
                mins, maxs = extents(verts + conn_verts)
        else:
            mins, maxs = extents(conn_verts)
        return Geometry(
            part=part_num,
            min_xyz=tuple(round(v, 3) for v in mins),
            max_xyz=tuple(round(v, 3) for v in maxs),
            connections=_cluster(conns),
            vertex_count=len(verts),
            unresolved=sorted(set(unresolved)),
        )


def _norm(v: tuple[float, float, float]) -> float:
    return (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5


def _parallel(u, v, tol: float = 1e-3) -> bool:
    """True if u and v are parallel or antiparallel (cross product ~ zero)."""
    nu, nv = _norm(u), _norm(v)
    if nu < 1e-9 or nv < 1e-9:
        return False
    cx = (u[1] * v[2] - u[2] * v[1],
          u[2] * v[0] - u[0] * v[2],
          u[0] * v[1] - u[1] * v[0])
    return _norm(cx) / (nu * nv) < tol


def _same_feature(a: Connection, b: Connection) -> bool:
    if a.kind != b.kind:
        return False
    d = (b.x - a.x, b.y - a.y, b.z - a.z)
    if _norm(d) <= CLUSTER_TOL:
        return True
    if a.kind in COLLINEAR_KINDS and _parallel(a.axis, b.axis):
        # Same axis line: two rim primitives of one through-hole.
        return _parallel(d, a.axis)
    return False


def _cluster(conns: list[Connection]) -> list[Connection]:
    """Merge multiple primitive references describing one physical feature.

    Raw reference counts overstate connections: a hole may be modelled as two
    rim primitives on opposite faces, and 32524 uses beamhole and peghole for
    different holes of the same beam. Positions and axes do not overstate.

    The merged feature is placed at the CENTROID of its members, not at
    whichever member happened to be read first. That matters as soon as
    anything is mounted on a hole: a beam models most holes with one primitive
    at mid-height but its end holes as two rims at +/-10 LDU, so keeping the
    first member put some holes 10 LDU off-centre. A Technic pin mounted on
    those sat 10 LDU low and protruded below the model's ground plane.
    """
    groups: list[list[Connection]] = []
    for c in conns:
        for g in groups:
            if _same_feature(g[0], c):
                g.append(c)
                break
        else:
            groups.append([c])

    out: list[Connection] = []
    for g in groups:
        n = len(g)
        out.append(Connection(
            kind=g[0].kind,
            x=sum(m.x for m in g) / n,
            y=sum(m.y for m in g) / n,
            z=sum(m.z for m in g) / n,
            axis=g[0].axis,
        ))
    return out


# ------------------------------------------------------------------- selftest

# Ground truth from LDraw's own unit conventions: a Brick 2x4 is 4 studs by
# 2 studs by 1 brick = 80 x 24 x 40 LDU with 8 studs on top, and so on.
FIXTURES = [
    # part,   (w_studs, h_plates, d_studs), {kind: count}
    ("3001",  (4, 3, 2), {"stud": 8, "socket": 3}),   # Brick 2 x 4
    ("3003",  (2, 3, 2), {"stud": 4, "socket": 1}),   # Brick 2 x 2
    ("3024",  (1, 1, 1), {"stud": 1}),                # Plate 1 x 1
    ("3023",  (2, 1, 1), {"stud": 2, "socket": 1}),   # Plate 1 x 2
    ("3005",  (1, 3, 1), {"stud": 1}),                # Brick 1 x 1
    # Technic beams are modelled with their long axis along Z, and are 20 LDU
    # (2.5 plates) tall. Rounded ends put the extreme vertex ~2 LDU inside the
    # nominal length, hence the 0.3-stud tolerance in the comparison below.
    ("32524", (1, 2.5, 7), {"pin_hole": 7}),          # Technic Beam 1 x 7 Thick
    ("32316", (1, 2.5, 5), {"pin_hole": 5}),          # Technic Beam 1 x 5 Thick
    # LDraw's 3701 is the 1 x 4 Technic brick (80 LDU long), not the 1 x 2.
    ("3701",  (4, 3, 1), {"stud": 4, "pin_hole": 3}),
]


def selftest() -> int:
    lib = LDrawLibrary()
    failures = []
    print(f"LDraw library: {len(lib._index):,} indexed .dat files\n")
    for part, want_size, want_conn in FIXTURES:
        try:
            g = lib.geometry(part)
        except (KeyError, ValueError) as e:
            print(f"  FAIL  {part}: {e}")
            failures.append(part)
            continue
        got_size = g.size_studs
        size_ok = all(abs(a - b) < 0.3 for a, b in zip(got_size, want_size))
        conn_ok = all(g.count(k) == v for k, v in want_conn.items())
        got_conn = {k: g.count(k) for k in want_conn}
        print(f"  {'PASS' if size_ok and conn_ok else 'FAIL'}  {g.summary()}")
        if not size_ok:
            print(f"        size: got {got_size}, want {want_size}")
        if not conn_ok:
            print(f"        connections: got {got_conn}, want {want_conn}")
        if g.unresolved:
            print(f"        unresolved refs: {g.unresolved[:4]}")
        if not (size_ok and conn_ok):
            failures.append(part)

    print()
    if failures:
        print(f"GATE FAILED: {len(failures)}/{len(FIXTURES)}: {', '.join(failures)}")
        return 1
    print(f"GATE PASSED: LDraw geometry parse verified on {len(FIXTURES)} fixtures.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if cmd == "selftest":
        sys.exit(selftest())
    elif cmd == "show":
        lib = LDrawLibrary()
        for p in sys.argv[2:]:
            g = lib.geometry(p)
            print(g.summary())
            print(f"  bbox {g.min_xyz} -> {g.max_xyz}  ({g.vertex_count:,} verts)")
            for c in g.connections[:16]:
                sx, sy, sz = c.studs()
                print(f"    {c.kind:10s} stud({sx:6.2f}, {sy:6.2f}, {sz:6.2f})")
            if g.unresolved:
                print(f"  unresolved: {g.unresolved}")
    else:
        raise SystemExit(__doc__)
