"""MOCForge Phase 4 - template fitting and build generation (L4).

A template declares ROLES (what kind of part is needed) and PLACEMENTS (where,
on a stud lattice). The fitter resolves roles against an owned inventory, places
concrete parts, validates the result, and writes an LDraw .ldr file.

Design principle: the template never names a specific part number. It says
"a plate at least 4 x 6" and the fitter picks from what the user owns. That is
what makes one template cover many inventories.

Coordinates. Lattice units are (x studs, level plates, z studs), with level 0 at
the ground and increasing upward. LDraw itself has +Y pointing DOWN and puts a
part's origin at its TOP face, so conversion is:

    origin_y = -(level * 8) - part_height_ldu

Validation is reported, never hidden. Every build carries a confidence score and
an itemised list of collisions, unsupported parts and unfilled roles, so a
tolerated error rate stays measurable rather than silent.

Usage:
  python generate.py list
  python generate.py build studded_car 10696-1
  python generate.py build studded_car 10696-1 42151-1   # combined inventory
"""

from __future__ import annotations

import math
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from inventory import Catalogue
from roles import classify
from ldraw import (LDU_PER_BRICK, LDU_PER_PLATE, LDU_PER_STUD, Geometry,
                   LDrawLibrary, ZIP_PATH)

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"

# Overlap below this many LDU is modelling tolerance, not a real collision.
# Studs protrude 4 LDU into the part above, which is intended, so the threshold
# must sit above that.
COLLISION_EPS = 5.0

# Fewer parts than this is not a model worth offering. Guards against a
# tiler-driven archetype returning an empty build that passes every
# validation check by vacuity.
MIN_PIECES = 4

# A part resting on less than this fraction of its own footprint is attached
# but precarious. Reported, and counted against confidence, rather than
# silently passed as buildable.
MIN_SUPPORT = 0.5

# A plate bearing on both sides still sags if the unsupported span is long.
# 12 studs is generous for a single plate thickness.
MAX_SPAN = 12.0


# --------------------------------------------------------------------- colours

class LDrawColours:
    """Maps a Rebrickable colour id to the nearest LDraw colour code by RGB.

    Nearest-RGB over the whole 322-entry LDConfig palette is wrong, because
    special finishes sit right next to ordinary ones in RGB space. Matching
    Light Bluish Gray (#969696) unrestricted returns 135 Pearl_Light_Grey
    (#A0A0A0), plain white returns 10047 Trans_Sticker, and yellow returns
    46 Trans_Yellow - so a model of solid bricks renders pearlescent and
    see-through. The palette is therefore split by material, and Rebrickable's
    own is_trans flag decides which half to search.
    """

    SPECIAL = ("PEARLESCENT", "CHROME", "RUBBER", "METAL", "MATERIAL",
               "LUMINANCE", "SPECKLE", "GLITTER")

    def __init__(self, zip_path: Path = ZIP_PATH):
        z = zipfile.ZipFile(zip_path)
        self.solid: dict[int, tuple[int, int, int]] = {}
        self.trans: dict[int, tuple[int, int, int]] = {}
        rx = re.compile(r"!COLOUR\s+(\S+).*?CODE\s+(\d+).*?VALUE\s+#([0-9A-Fa-f]{6})")
        for line in z.read("ldraw/LDConfig.ldr").decode("latin-1").splitlines():
            m = rx.search(line)
            if not m:
                continue
            code, hexv = int(m.group(2)), m.group(3)
            # Codes at 1000 and above are internal/auxiliary definitions.
            if code >= 1000 or any(k in line for k in self.SPECIAL):
                continue
            value = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
            (self.trans if "ALPHA" in line else self.solid)[code] = value
        self._cache: dict[int, int] = {}

    def for_rebrickable(self, color_id: int, rgb: str | None,
                        is_trans: bool = False) -> int:
        if color_id in self._cache:
            return self._cache[color_id]
        code = 16  # LDraw "main colour" - a safe neutral default
        palette = self.trans if is_trans else self.solid
        if rgb and len(rgb) == 6 and palette:
            try:
                target = tuple(int(rgb[i:i + 2], 16) for i in (0, 2, 4))
                code = min(palette, key=lambda c: sum(
                    (a - b) ** 2 for a, b in zip(palette[c], target)))
            except ValueError:
                pass
        self._cache[color_id] = code
        return code


# ----------------------------------------------------------------- template DSL

@dataclass
class Candidate:
    """An owned part that could fill a role."""
    part_num: str
    color_id: int
    quantity: int
    name: str
    geom: Geometry

    @property
    def footprint(self) -> tuple[float, float]:
        w, _h, d = self.geom.size_studs
        return (w, d)


@dataclass
class Role:
    name: str
    match: Callable[[Candidate], bool]
    describe: str = ""
    prefer_largest: bool = False
    prefer_stock: bool = False
    # Rank by how many are owned before size. A crib's height is
    # limited by the stock of one beam length, not by its reach.
    optional: bool = False
    # An optional role that cannot be filled does not sink the build - its
    # placements are simply dropped and a warning is recorded. A car missing
    # its windscreen is still a car; today a single unfillable role produced
    # nothing at all, which is why so many inventories returned zero designs.

    def __post_init__(self):
        if not self.describe:
            self.describe = self.name


@dataclass
class Placement:
    role: str
    x: float            # lattice x, in studs, of the footprint's low corner
    z: float            # lattice z, in studs
    level: float        # plates above ground
    rot: object = 0     # key into ROT
    tag: str = ""
    mount_on: tuple | None = None
    # mount_on = (host_tag, connection_kind, index): attach to the host's Nth
    # connection point of that kind. Lets a wheel sit exactly on a wheel-holder
    # pin whatever wheel the fitter picks, rather than trusting a level computed
    # for one assumed diameter.
    part: Candidate | None = None
    # Set by the tiler, which chooses concrete parts dynamically rather than
    # through a fixed role. Overrides the role lookup when present.
    seat: float = 1.0
    # How far along the connection axis to shift the part, as a multiple of half
    # its own extent:
    #   +1  outboard - a wheel on a HOLDER PIN sits beyond the pin, so its inner
    #       face meets the pin. Centring it instead drives half the wheel back
    #       through the bodywork.
    #   -1  inboard - a wheel on an AXLE has the axle running through it, so the
    #       axle tip should reach the wheel's outer face.
    #    0  centred on the connection point, for a shaft through a hole.
    align: str = "bottom"
    # "bottom": the part's underside sits at `level`.
    # "top":    the part's top sits at `level`, so it hangs below. Needed for
    #           parts mounted under a plate - 4600's wheel holders extend well
    #           below its studded surface, so aligning its bottom to the ground
    #           pushes the plate itself too high for the chassis to rest on.
    note: str = ""


@dataclass
class Template:
    """A parametric archetype.

    `counts` declares how many of each role are needed, which the fitter must
    know before it picks parts. `layout` then produces placements *from the
    chosen parts*, so positions can depend on real geometry - a wheel-holder's
    height has to follow the radius of whichever wheel was selected, and no
    fixed number can be right for every inventory.
    """
    name: str
    title: str
    roles: dict[str, Role]
    counts: dict[str, int]
    layout: Callable[..., list[Placement]]
    variants: tuple[dict, ...] = ({},)
    # Each variant is a parameter set the layout is run with, so one
    # archetype yields several genuinely different designs from one
    # inventory. This is what makes the output 'a series' rather than one.
    studded: bool = True

    def demand(self) -> dict[str, int]:
        return dict(self.counts)


# --------------------------------------------------------------------- results

@dataclass
class PlacedPart:
    part_num: str
    ldraw_id: str
    colour: int
    role: str
    matrix: tuple
    pos: tuple[float, float, float]
    bbox: tuple[tuple[float, float, float], tuple[float, float, float]]
    level: float
    note: str = ""
    geom: Geometry | None = None
    host: str = ""      # tag of the part this one is mounted on, if any
    tag: str = ""
    color_id: int = -1  # Rebrickable colour id, kept so consumers need not
    # reverse-map the LDraw code back through the owned lots to name a colour.
    family: str = ""    # taxonomy family, so a renderer can tell a wheel from
    # a brick without re-deriving it from a part name it does not carry.


@dataclass
class Build:
    template: str
    parts: list[PlacedPart] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    assignments: dict[str, Candidate] = field(default_factory=dict)

    @property
    def piece_count(self) -> int:
        return len(self.parts)

    @property
    def buildable(self) -> bool:
        # A build with no parts passes every other check trivially - no
        # unfilled roles, no collisions, nothing unsupported - and was being
        # offered to the user as a valid model. A tiler-driven archetype
        # returns nothing when the inventory cannot size it, so this guard is
        # what stops an empty file being presented as a design.
        if len(self.parts) < MIN_PIECES:
            return False
        # `weak` blocks too. It previously did not, so a build could be
        # "buildable" while scoring 0.971 - and CONTRACT.md promises the client
        # that confidence is always 1.0. Measured across 60 sampled sets: 236
        # designs offered, 0 with a weak part, so making this strict costs no
        # coverage. Enforcing it at generation time is what made that true.
        return not (self.unfilled or self.collisions
                    or self.unsupported or self.weak)

    @property
    def confidence(self) -> float:
        """1.0 only when every check passes. Each defect class costs weight."""
        if self.unfilled or len(self.parts) < MIN_PIECES:
            return 0.0
        total = max(len(self.parts), 1)
        score = 1.0
        score -= 0.6 * len(self.collisions) / total
        score -= 0.4 * len(self.unsupported) / total
        score -= 0.2 * len(self.weak) / total
        return max(0.0, round(score, 3))

    def report(self) -> str:
        lines = [
            f"template   : {self.template}",
            f"pieces      : {self.piece_count}",
            f"buildable   : {'YES' if self.buildable else 'NO'}",
            f"confidence  : {self.confidence:.3f}",
        ]
        if self.unfilled:
            lines.append(f"UNFILLED ROLES ({len(self.unfilled)}):")
            lines += [f"    - {u}" for u in self.unfilled]
        if self.collisions:
            lines.append(f"COLLISIONS ({len(self.collisions)}):")
            lines += [f"    - {c}" for c in self.collisions[:10]]
        if self.unsupported:
            lines.append(f"UNSUPPORTED ({len(self.unsupported)}):")
            lines += [f"    - {u}" for u in self.unsupported[:10]]
        if self.weak:
            lines.append(f"WEAKLY SUPPORTED ({len(self.weak)}):")
            lines += [f"    - {w}" for w in self.weak[:8]]
        if self.warnings:
            lines.append(f"warnings ({len(self.warnings)}):")
            lines += [f"    - {w}" for w in self.warnings[:10]]
        return "\n".join(lines)


# ---------------------------------------------------------------------- fitter

ROT = {
    # About Y - the usual case, spinning a part flat on the lattice.
    0:   (1, 0, 0, 0, 1, 0, 0, 0, 1),
    90:  (0, 0, 1, 0, 1, 0, -1, 0, 0),
    180: (-1, 0, 0, 0, 1, 0, 0, 0, -1),
    270: (0, 0, -1, 0, 1, 0, 1, 0, 0),
    # About X - needed for wheels. A wheel's local +Y is its spin axis, and
    # Rx(90) maps (0,1,0) to (0,0,1), pointing the axis across the car's width.
    "x90":  (1, 0, 0, 0, 0, -1, 0, 1, 0),
    "x270": (1, 0, 0, 0, 0, 1, 0, -1, 0),
    # Technic chassis rail. A beam is authored with its long axis along Z and
    # its pin-hole axis along Y, so laid flat its holes point up - useless for
    # mounting an axle across a vehicle. This cyclic rotation sends
    # ez -> ex (long axis becomes the car's length) and ey -> ez (hole axis
    # becomes the car's width), which is how a beam is used as a side rail.
    "rail": (0, 0, 1, 1, 0, 0, 0, 1, 0),
    # About Z, sending ex -> ey: stands a Technic pin upright so it can join two
    # stacked beam courses. A pin is authored along its own X.
    "z90": (0, -1, 0, 1, 0, 0, 0, 0, 1),
}


def _is_connector(p: PlacedPart) -> bool:
    """True for parts whose job is to occupy a hole in something else."""
    return p.role in ("pin", "cross_axle", "axle") or (
        p.geom is not None
        and (p.part_num.startswith(("61332", "2780"))
             or p.role.endswith("_pin")))


def _rotate_bbox(mn, mx, rot):
    """Rotate an axis-aligned box about Y and return the new AABB."""
    m = ROT[rot]
    corners = [(x, y, z) for x in (mn[0], mx[0]) for y in (mn[1], mx[1])
               for z in (mn[2], mx[2])]
    pts = [(m[0] * x + m[1] * y + m[2] * z,
            m[3] * x + m[4] * y + m[5] * z,
            m[6] * x + m[7] * y + m[8] * z) for x, y, z in corners]
    return (tuple(min(p[k] for p in pts) for k in range(3)),
            tuple(max(p[k] for p in pts) for k in range(3)))


class Fitter:
    def __init__(self, cat: Catalogue, lib: LDrawLibrary):
        self.cat = cat
        self.lib = lib
        self.colours = LDrawColours()
        self._names = {p: n for p, n in cat.conn.execute("SELECT part_num, name FROM parts")}
        self._rgb = {i: r for i, r in cat.conn.execute("SELECT id, rgb FROM colors")}
        self._trans = {i: bool(t) for i, t in cat.conn.execute(
            "SELECT id, is_trans FROM colors")}
        self._ldmap = {p: l for p, l in cat.conn.execute(
            "SELECT part_num, ldraw_id FROM ldraw_map")}

    def candidates(self, lots) -> list[Candidate]:
        out = []
        for (part_num, color_id), qty in lots.items():
            lid = self._ldmap.get(part_num, part_num)
            try:
                geom = self.lib.geometry(lid)
            except Exception:
                continue  # no geometry: treat as unavailable, a safe failure
            out.append(Candidate(part_num, color_id, qty,
                                 self._names.get(part_num, "?"), geom))
        return out

    def fit(self, tpl: Template, lots, params: dict | None = None) -> Build:
        cands = self.candidates(lots)
        label = tpl.name + (("/" + params["name"]) if params and "name" in params else "")
        build = Build(template=label)
        demand = tpl.demand()
        used: dict[tuple[str, int], int] = {}
        skipped: set[str] = set()   # optional roles that could not be filled

        # Assign one concrete part per role, respecting how many are needed.
        for role_name, role in tpl.roles.items():
            need = demand.get(role_name, 0)
            if need == 0:
                continue
            pool = [c for c in cands if role.match(c)]
            pool = [c for c in pool if c.quantity - used.get((c.part_num, c.color_id), 0) >= need]
            if not pool:
                if role.optional:
                    build.warnings.append(
                        f"optional role {role.describe!r} unfilled; omitted")
                    skipped.add(role_name)
                else:
                    build.unfilled.append(f"{role.describe} (need {need})")
                continue
            # Rank by the part's longest dimension, not footprint area: an
            # axle is 12 LDU square whatever its length, so area cannot
            # distinguish an Axle 5 from an Axle 11.
            def _extent(c: Candidate) -> float:
                return max(c.geom.size_ldu)

            pool.sort(key=lambda c: (
                -c.quantity if role.prefer_stock else 0,
                -_extent(c) if role.prefer_largest else _extent(c),
                -c.quantity,
            ))
            chosen = pool[0]
            build.assignments[role_name] = chosen
            used[(chosen.part_num, chosen.color_id)] = \
                used.get((chosen.part_num, chosen.color_id), 0) + need

        if build.unfilled:
            return build

        # Positions are derived from the parts actually chosen. The pool is
        # passed too, so a layout can tile a region from whatever is spare
        # instead of demanding one exact part number.
        spare = [
            Candidate(c.part_num, c.color_id,
                      c.quantity - used.get((c.part_num, c.color_id), 0),
                      c.name, c.geom)
            for c in cands
            if c.quantity - used.get((c.part_num, c.color_id), 0) > 0
        ]
        placements = tpl.layout(build.assignments, spare, params or {})

        # Two passes: lattice-positioned parts first, then anything mounted on
        # one of them, since a mount needs its host already placed.
        by_tag: dict[str, PlacedPart] = {}
        for pl in sorted(placements, key=lambda p: p.mount_on is not None):
            if pl.role in skipped:
                continue   # optional role omitted; drop its placements
            c = pl.part or build.assignments.get(pl.role)
            if c is None:
                build.unfilled.append(f"{pl.note}: role {pl.role!r} unassigned")
                continue
            mn, mx = _rotate_bbox(c.geom.min_xyz, c.geom.max_xyz, pl.rot)

            if pl.mount_on is None:
                w = (mx[0] - mn[0]) / LDU_PER_STUD
                d = (mx[2] - mn[2]) / LDU_PER_STUD
                # Land the footprint on the intended lattice cells.
                off_x = (pl.x + w / 2) * LDU_PER_STUD - (mn[0] + mx[0]) / 2
                off_z = (pl.z + d / 2) * LDU_PER_STUD - (mn[2] + mx[2]) / 2
                # +Y is down: a part's lowest point is max Y, highest is min Y.
                edge = mx[1] if pl.align == "bottom" else mn[1]
                off_y = -(pl.level * LDU_PER_PLATE) - edge
            else:
                host_tag, kind, idx = pl.mount_on
                host = by_tag.get(host_tag)
                if host is None or host.geom is None:
                    build.unfilled.append(f"{pl.note}: no host tagged {host_tag!r}")
                    continue
                pts = [cn for cn in host.geom.connections if cn.kind == kind]
                if idx >= len(pts):
                    build.unfilled.append(
                        f"{pl.note}: host {host.part_num} has {len(pts)} "
                        f"{kind} points, need index {idx}")
                    continue
                cn = pts[idx]
                m = host.matrix
                target = [
                    m[0] * cn.x + m[1] * cn.y + m[2] * cn.z + host.pos[0],
                    m[3] * cn.x + m[4] * cn.y + m[5] * cn.z + host.pos[1],
                    m[6] * cn.x + m[7] * cn.y + m[8] * cn.z + host.pos[2],
                ]
                if pl.seat:  # 0.0 means centre on the point, so skip

                    # The connection's axis in world space.
                    ax = (m[0] * cn.axis[0] + m[1] * cn.axis[1] + m[2] * cn.axis[2],
                          m[3] * cn.axis[0] + m[4] * cn.axis[1] + m[5] * cn.axis[2],
                          m[6] * cn.axis[0] + m[7] * cn.axis[1] + m[8] * cn.axis[2])
                    n = (ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2) ** 0.5
                    if n > 1e-9:
                        u = [ax[0] / n, ax[1] / n, ax[2] / n]
                        # LDraw's recorded axes point INWARD for 4600's pins
                        # (the pin at local x=+22 carries axis -1,0,0), so the
                        # axis alone does not say which way is outboard. Resolve
                        # it geometrically: outboard is away from the host's
                        # centre. This holds however the primitive was authored.
                        hc = [(host.bbox[0][k] + host.bbox[1][k]) / 2 for k in range(3)]
                        out = [target[k] - hc[k] for k in range(3)]
                        if sum(u[k] * out[k] for k in range(3)) < 0:
                            u = [-v for v in u]
                        size = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
                        reach = sum(abs(u[k]) * size[k] for k in range(3)) / 2
                        for k in range(3):
                            target[k] += u[k] * reach * pl.seat
                off_x = target[0] - (mn[0] + mx[0]) / 2
                off_y = target[1] - (mn[1] + mx[1]) / 2
                off_z = target[2] - (mn[2] + mx[2]) / 2

            pos = (round(off_x, 3), round(off_y, 3), round(off_z, 3))
            abs_bbox = (
                (mn[0] + off_x, mn[1] + off_y, mn[2] + off_z),
                (mx[0] + off_x, mx[1] + off_y, mx[2] + off_z),
            )
            placed = PlacedPart(
                part_num=c.part_num,
                ldraw_id=self._ldmap.get(c.part_num, c.part_num),
                colour=self.colours.for_rebrickable(c.color_id, self._rgb.get(c.color_id),
                                                   self._trans.get(c.color_id, False)),
                role=pl.role, matrix=ROT[pl.rot], pos=pos, bbox=abs_bbox,
                level=pl.level, note=pl.note, geom=c.geom, tag=pl.tag,
                color_id=c.color_id, family=family_of(c),
                host=pl.mount_on[0] if pl.mount_on else "",
            )
            build.parts.append(placed)
            if pl.tag:
                by_tag[pl.tag] = placed

        self._validate(build)
        return build

    def _validate(self, build: Build) -> None:
        parts = build.parts
        # Collisions: solid interpenetration beyond stud tolerance. A part and
        # the part it is mounted on are exempt - a wheel is meant to overlap
        # the pin it sits on.
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                a, b = parts[i], parts[j]
                if (a.host and a.host == b.tag) or (b.host and b.host == a.tag):
                    continue
                # A pin or axle inserted through a hole necessarily overlaps the
                # part it passes through - that is what inserting means, and it
                # is true of every part it threads, not only its declared host.
                # The collision test exists to catch two STRUCTURAL parts
                # claiming the same space, so connectors are exempt.
                if _is_connector(a) or _is_connector(b):
                    continue
                overlap = [
                    min(a.bbox[1][k], b.bbox[1][k]) - max(a.bbox[0][k], b.bbox[0][k])
                    for k in range(3)
                ]
                if all(o > COLLISION_EPS for o in overlap):
                    build.collisions.append(
                        f"{a.part_num}({a.role}) vs {b.part_num}({b.role}) "
                        f"overlap {tuple(round(o, 1) for o in overlap)} LDU"
                    )

        # How much of each part actually rests on material below. "Connected"
        # only required >1 LDU of overlap, which a brick hanging on by a single
        # stud satisfies - technically attached, but not something you would
        # hand to someone as buildable. This measures it properly.
        mounted_hosts = {o.host for o in parts if o.host}
        for p in parts:
            if p.bbox[1][1] >= -0.5 or p.host:
                continue    # on the ground, or itself explicitly mounted
            if p.tag and p.tag in mounted_hosts:
                # Something is mounted ON this part, which holds it. The Technic
                # spine is carried by the axles running through it, so measuring
                # only what lies beneath called it a 0%-supported cantilever.
                continue
            p_top = p.bbox[0][1]
            if any(o is not p and abs(o.bbox[1][1] - p_top) <= 1.5
                   and min(o.bbox[1][0], p.bbox[1][0]) - max(o.bbox[0][0], p.bbox[0][0]) > 1.0
                   and min(o.bbox[1][2], p.bbox[1][2]) - max(o.bbox[0][2], p.bbox[0][2]) > 1.0
                   for o in parts):
                # Held from above - a wheel-holder plate hangs under the chassis
                # on its studs, which is a real connection, not a cantilever.
                continue
            own = ((p.bbox[1][0] - p.bbox[0][0]) * (p.bbox[1][2] - p.bbox[0][2]))
            if own <= 0:
                continue
            base_y = p.bbox[1][1]
            covered = 0.0
            for o in parts:
                if o is p or abs(o.bbox[0][1] - base_y) > 1.5:
                    continue
                dx = min(o.bbox[1][0], p.bbox[1][0]) - max(o.bbox[0][0], p.bbox[0][0])
                dz = min(o.bbox[1][2], p.bbox[1][2]) - max(o.bbox[0][2], p.bbox[0][2])
                if dx > 0 and dz > 0:
                    covered += dx * dz
            frac = min(1.0, covered / own)
            if frac >= MIN_SUPPORT:
                continue

            # Low coverage is not automatically bad. A roof plate BRIDGES a
            # room: it is held along two opposite edges and spans the gap
            # between, which is sound construction and measured only 12-34%
            # covered. A plate held along ONE edge is a cantilever, which is
            # not. So check which edges bear, and how far the span is.
            supports = [o for o in parts
                        if o is not p and abs(o.bbox[0][1] - base_y) <= 1.5]
            edge = {"x0": False, "x1": False, "z0": False, "z1": False}
            for o in supports:
                dx = min(o.bbox[1][0], p.bbox[1][0]) - max(o.bbox[0][0], p.bbox[0][0])
                dz = min(o.bbox[1][2], p.bbox[1][2]) - max(o.bbox[0][2], p.bbox[0][2])
                if dx <= 0 or dz <= 0:
                    continue
                if o.bbox[0][0] <= p.bbox[0][0] + LDU_PER_STUD:
                    edge["x0"] = True
                if o.bbox[1][0] >= p.bbox[1][0] - LDU_PER_STUD:
                    edge["x1"] = True
                if o.bbox[0][2] <= p.bbox[0][2] + LDU_PER_STUD:
                    edge["z0"] = True
                if o.bbox[1][2] >= p.bbox[1][2] - LDU_PER_STUD:
                    edge["z1"] = True

            spans_x = edge["x0"] and edge["x1"]
            spans_z = edge["z0"] and edge["z1"]
            if spans_x or spans_z:
                # Bearing on both sides. Still only sound up to a limit: a long
                # plate unsupported across its middle sags and pops off.
                free = ((p.bbox[1][0] - p.bbox[0][0]) if spans_x
                        else (p.bbox[1][2] - p.bbox[0][2])) / LDU_PER_STUD
                if free > MAX_SPAN:
                    build.weak.append(
                        f"{p.part_num}({p.note or p.role}) spans {free:.0f} studs "
                        f"unsupported (limit {MAX_SPAN})")
                continue

            build.weak.append(
                f"{p.part_num}({p.note or p.role}) cantilevered, only "
                f"{frac:.0%} supported")

        # Soft check: does the model actually sit on the ground? A wheel whose
        # radius does not match the pin height leaves the car floating or sunk.
        # Reported as a warning, not a defect - it affects looks, not buildability.
        lowest = max((p.bbox[1][1] for p in parts), default=0.0)
        if abs(lowest) > 2.0:
            build.warnings.append(
                f"lowest point is {lowest:.1f} LDU vs ground plane 0 "
                f"({'sunk below' if lowest > 0 else 'floating above'} ground)"
            )
        # Connection: every part must touch another part with overlapping
        # footprint, either below it or above it. Contact from above counts -
        # a wheel-holder plate hangs from the chassis and is held by the studs
        # going up into it, which is a legitimate LEGO connection.
        tags = {p.tag for p in parts if p.tag}
        for p in parts:
            if p.bbox[1][1] >= -0.5:
                continue  # resting on the ground plane
            if p.host and p.host in tags:
                continue  # an explicit mount is a connection by construction
            if p.tag and any(o.host == p.tag for o in parts):
                continue  # something is mounted on it, which connects it too
            p_bot, p_top = p.bbox[1][1], p.bbox[0][1]
            connected = False
            for o in parts:
                if o is p:
                    continue
                dx = min(o.bbox[1][0], p.bbox[1][0]) - max(o.bbox[0][0], p.bbox[0][0])
                dz = min(o.bbox[1][2], p.bbox[1][2]) - max(o.bbox[0][2], p.bbox[0][2])
                if dx <= 1.0 or dz <= 1.0:
                    continue
                if abs(o.bbox[0][1] - p_bot) < 1.5 or abs(o.bbox[1][1] - p_top) < 1.5:
                    connected = True
                    break
            if not connected:
                build.unsupported.append(
                    f"{p.part_num}({p.role}) at level {p.level:g} has no neighbour"
                )

    # ------------------------------------------------------------------ output

    def write_ldr(self, build: Build, path: Path, title: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.ldr_text(build, title, path.name), encoding="utf-8")
        return path

    def ldr_text(self, build: Build, title: str, name: str) -> str:
        """Render a build as LDraw text.

        Split out from write_ldr so a caller that wants the string - an HTTP
        response, say - does not have to write a file and read it back. The
        attribution notice (R4/R5) lives here and nowhere else, so it cannot
        drift between the two paths.
        """
        lines = [
            f"0 {title}",
            f"0 Name: {name}",
            "0 Author: MOCForge (generated)",
            "0 !LDRAW_ORG Unofficial_Model",
            "0 // Catalogue data from Rebrickable. Geometry from the LDraw Parts",
            "0 // Library, CC BY 2.0. LEGO is a trademark of the LEGO Group,",
            "0 // which does not sponsor or endorse this model.",
            f"0 // confidence {build.confidence:.3f}  pieces {build.piece_count}",
        ]
        for p in sorted(build.parts, key=lambda q: (q.level, q.role)):
            m = " ".join(f"{v:g}" for v in p.matrix)
            x, y, z = p.pos
            lines.append(f"1 {p.colour} {x:g} {y:g} {z:g} {m} {p.ldraw_id}.dat")
        return "\n".join(lines) + "\n"

    def instructions(self, build: Build) -> str:
        """Group placements into build steps by level - the natural build order."""
        by_level: dict[int, list[PlacedPart]] = {}
        for p in build.parts:
            by_level.setdefault(p.level, []).append(p)
        out = []
        for step, level in enumerate(sorted(by_level), start=1):
            group = by_level[level]
            out.append(f"Step {step} - level {level} ({len(group)} parts)")
            counts: dict[tuple, int] = {}
            for p in group:
                counts[(p.part_num, p.role, p.note)] = counts.get((p.part_num, p.role, p.note), 0) + 1
            for (pn, role, note), n in sorted(counts.items(), key=lambda kv: -kv[1]):
                label = self._names.get(pn, "?")[:44]
                out.append(f"    {n} x {pn:>10}  {label:<44} [{note or role}]")
        return "\n".join(out)


# ------------------------------------------------------------------- templates

def _is(pattern: str):
    rx = re.compile(pattern, re.I)
    return lambda c: bool(rx.search(c.name))


def _plate_between(lo: tuple[float, float], hi: tuple[float, float]):
    """Plates whose footprint falls within a range, in either orientation.

    An upper bound matters: without one, "at least 4x6" happily selects the
    Plate 8x16 in a creative brick box, which then overhangs the whole car.
    """
    def pred(c: Candidate) -> bool:
        if not re.match(r"^Plate \d+ x \d+$", c.name):
            return False
        fw, fd = c.footprint
        for a, b in ((fw, fd), (fd, fw)):
            if lo[0] - 0.2 <= a <= hi[0] + 0.2 and lo[1] - 0.2 <= b <= hi[1] + 0.2:
                return True
        return False
    return pred


def _brick_exact(w: float, d: float):
    def pred(c: Candidate) -> bool:
        if not re.match(r"^Brick \d+ x \d+$", c.name):
            return False
        fw, fd = c.footprint
        return (abs(fw - w) < 0.2 and abs(fd - d) < 0.2) or \
               (abs(fw - d) < 0.2 and abs(fd - w) < 0.2)
    return pred


class Budget:
    """Remaining quantity per owned lot, shared across successive tiler calls.

    Essential for multi-course work: tiling each wall course with its own
    private quantity map would spend the same bricks over and over, producing a
    model the user cannot actually build. One budget threaded through every call
    keeps total consumption inside what is owned.
    """

    def __init__(self, pool: list[Candidate]):
        self.left: dict[tuple[str, int], int] = {
            (c.part_num, c.color_id): c.quantity for c in pool}

    def take(self, c: Candidate) -> bool:
        k = (c.part_num, c.color_id)
        if self.left.get(k, 0) <= 0:
            return False
        self.left[k] -= 1
        return True

    def has(self, c: Candidate) -> bool:
        return self.left.get((c.part_num, c.color_id), 0) > 0

    def spent(self) -> int:
        return sum(max(0, v) for v in self.left.values())


def solid_cells(w: int, d: int) -> set[tuple[int, int]]:
    return {(x, z) for x in range(w) for z in range(d)}


def ring_cells(w: int, d: int) -> set[tuple[int, int]]:
    """The perimeter of a w x d footprint - a one-stud-thick wall."""
    return {(x, z) for x in range(w) for z in range(d)
            if x in (0, w - 1) or z in (0, d - 1)}


def _tile_cells(pool: list[Candidate], cells: set[tuple[int, int]], level: float,
                accept: Callable[[Candidate], bool], note: str, budget: Budget,
                shift: int = 0,
                must_touch: set[tuple[int, int]] | None = None,
                span_touch: bool = False,
                palette: frozenset[int] | None = None) -> list[Placement]:
    """Greedily fill an arbitrary set of lattice cells with available parts.

    Generalises rectangle filling to any mask, which is what walls need: a
    building's course is a ring, not a solid block. A part is only placed when
    every cell it would occupy is inside the mask, so bricks never overhang a
    wall or bridge a doorway.

    `shift` rotates the scan order between courses so joints stagger rather than
    stacking in a column, which is both stronger and looks like real brickwork.
    """
    stock = []
    for c in pool:
        if not accept(c):
            continue
        fw, fd = c.footprint
        if abs(fw - round(fw)) > 0.2 or abs(fd - round(fd)) > 0.2:
            continue
        stock.append((c, int(round(fw)), int(round(fd))))
    # Largest first so big parts carry the bulk. Within that, prefer the chosen
    # palette: without steering, a creative brick box yields a model in a dozen
    # unrelated colours, which reads as confetti rather than a design.
    if palette:
        stock.sort(key=lambda s: (s[0].color_id not in palette, -(s[1] * s[2])))
    else:
        stock.sort(key=lambda s: -(s[1] * s[2]))

    free = set(cells)
    order = sorted(cells)
    if shift and order:
        k = shift % len(order)
        order = order[k:] + order[:k]

    out: list[Placement] = []
    for (x, z) in order:
        if (x, z) not in free:
            continue
        for c, bw, bd in stock:
            if not budget.has(c):
                continue
            for pw, pd, rot in ((bw, bd, 0), (bd, bw, 90)):
                want = {(x + i, z + j) for i in range(pw) for j in range(pd)}
                if not want <= free:
                    continue
                # A roof over a hollow footprint must rest on the walls. Without
                # this, the tiler happily drops a plate in the middle of the
                # room with nothing underneath it.
                if must_touch is not None:
                    touched = want & must_touch
                    if not touched:
                        continue
                    if span_touch:
                        # Bear on OPPOSITE sides, not merely on two cells of the
                        # same wall. This is the identical criterion the
                        # stability pass applies, so the tiler can no longer
                        # place something the validator will then flag.
                        xs = {c[0] for c in touched}
                        zs = {c[1] for c in touched}
                        wx = {c[0] for c in want}
                        wz = {c[1] for c in want}
                        if not ((min(wx) in xs and max(wx) in xs)
                                or (min(wz) in zs and max(wz) in zs)):
                            continue
                if not budget.take(c):
                    continue
                free -= want
                out.append(Placement(role="_tiled", x=x, z=z, level=level,
                                     rot=rot, part=c, note=note))
                break
            else:
                continue
            break
    return out


def _tile_rect(pool: list[Candidate], w: int, d: int, level: float,
               accept: Callable[[Candidate], bool], note: str,
               budget: Budget | None = None) -> list[Placement]:
    """Fill a solid w x d rectangle. Thin wrapper over _tile_cells."""
    return _tile_cells(pool, solid_cells(w, d), level, accept, note,
                       budget or Budget(pool))


def _car_layout(a: dict[str, Candidate], pool: list[Candidate],
                params: dict | None = None) -> list[Placement]:
    """Lay out a 2-wide studded car, deriving every height from real geometry.

    The wheel-holder plate must sit at whatever height puts its pins exactly one
    wheel-radius above the ground, or the car either floats or sinks. Solving it:

      4600's pins are at local y=5 and its underside at local y=8, so with the
      underside placed at level L the pins land at y = -(L*8) - 3. Setting that
      equal to -radius gives  L = (radius - 3) / 8.

    Everything above then stacks off the holder's measured height.
    """
    wheel, holder, chassis = a["wheel"], a["wheel_holder"], a["chassis"]

    # Radius is the wheel's extent perpendicular to its spin axis (along Z).
    wx, _wh, _wd = wheel.geom.size_ldu
    wy = wheel.geom.max_xyz[1] - wheel.geom.min_xyz[1]
    radius = max(wx, wy) / 2

    # Pin height above the holder's underside, measured rather than assumed.
    pins = [c for c in holder.geom.connections if c.kind == "wheel_pin"]
    pin_up = (holder.geom.max_xyz[1] - pins[0].y) if pins else 3.0

    holder_level = max(0.0, (radius - pin_up) / LDU_PER_PLATE)
    holder_h = (holder.geom.max_xyz[1] - holder.geom.min_xyz[1]) / LDU_PER_PLATE
    chassis_level = holder_level + holder_h
    chassis_h = (chassis.geom.max_xyz[1] - chassis.geom.min_xyz[1]) / LDU_PER_PLATE
    body_level = chassis_level + chassis_h

    # Centre the holder's rotated footprint on the 2-stud-wide body.
    hw, hd = holder.footprint          # unrotated
    holder_z = (2 - hw) / 2            # after rot=90 the x extent becomes depth

    return [
        Placement("wheel_holder", x=0.5, z=holder_z, level=holder_level, rot=90,
                  tag="rear_axle", note="rear axle"),
        Placement("wheel_holder", x=3.5, z=holder_z, level=holder_level, rot=90,
                  tag="front_axle", note="front axle"),
        # Wheels seat on the pins, pushed outboard by half their own width.
        Placement("wheel", 0, 0, 0, mount_on=("rear_axle", "wheel_pin", 0),
                  note="rear left wheel"),
        Placement("wheel", 0, 0, 0, mount_on=("rear_axle", "wheel_pin", 1),
                  note="rear right wheel"),
        Placement("wheel", 0, 0, 0, mount_on=("front_axle", "wheel_pin", 0),
                  note="front left wheel"),
        Placement("wheel", 0, 0, 0, mount_on=("front_axle", "wheel_pin", 1),
                  note="front right wheel"),
        Placement("chassis", x=0, z=0, level=chassis_level, note="chassis"),
    ] + _tile_rect(pool, w=6, d=2, level=body_level,
                   accept=is_wall_brick, note="body")


STUDDED_CAR = Template(
    name="studded_car",
    title="MOCForge 4-wide car",
    roles={
        # Defined by measured function, not by name: any part carrying two or
        # more wheel pins is a wheel holder. Matching on the name
        # "Plate Special 2 x 2 with Wheel Holders" rejected perfectly good
        # alternatives such as 2926 "Plate Special 1 x 4 with Wheels Holder",
        # and that single predicate was the sole reason 5 of 9 test inventories
        # produced nothing at all.
        "wheel_holder": Role("wheel_holder",
                             lambda c: c.geom.count("wheel_pin") >= 2,
                             "any part with 2+ wheel pins"),
        "wheel":        Role("wheel", _is(r"^Wheel \d"), "Wheel", prefer_largest=True),
        "chassis":      Role("chassis", _plate_between((2, 6), (2, 6)), "Plate 2x6"),
    },
    counts={"wheel_holder": 2, "wheel": 4, "chassis": 1},
    layout=_car_layout,
)

def _axle_car_layout(a: dict[str, Candidate], pool: list[Candidate],
                     params: dict | None = None) -> list[Placement]:
    """A car whose wheels ride on axles through Technic bricks.

    Needed because many studded sets carry no wheel-pin part at all: they mount
    wheels with an axle passed through a brick's axle hole. That accounted for
    every remaining failure of the pin-based archetype.

    Heights are solved the same way as the pin version, but from the brick's
    axle-hole position instead of a pin:

      with the brick's underside at level L, the hole lands at
      y = -(L*8) - (brick_height - hole_y), so setting that to -radius gives
      L = (radius - hole_up) / 8.
    """
    wheel, brick, chassis = a["wheel"], a["axle_brick"], a["chassis"]

    wx = wheel.geom.max_xyz[0] - wheel.geom.min_xyz[0]
    wy = wheel.geom.max_xyz[1] - wheel.geom.min_xyz[1]
    radius = max(wx, wy) / 2

    holes = [c for c in brick.geom.connections if c.kind == "axle_hole"]
    hole_up = brick.geom.max_xyz[1] - holes[0].y if holes else 12.0
    brick_level = max(0.0, (radius - hole_up) / LDU_PER_PLATE)
    brick_h = (brick.geom.max_xyz[1] - brick.geom.min_xyz[1]) / LDU_PER_PLATE
    chassis_level = brick_level + brick_h
    chassis_h = (chassis.geom.max_xyz[1] - chassis.geom.min_xyz[1]) / LDU_PER_PLATE
    body_level = chassis_level + chassis_h

    bw, bd = brick.footprint
    brick_z = (2 - bd) / 2

    out = [
        Placement("axle_brick", x=0, z=brick_z, level=brick_level,
                  tag="rear_hub", note="rear axle brick"),
        Placement("axle_brick", x=4, z=brick_z, level=brick_level,
                  tag="front_hub", note="front axle brick"),
        # Axle centred in the hole and turned to run across the car.
        Placement("axle", 0, 0, 0, rot=90, seat=0.0, tag="rear_axle",
                  mount_on=("rear_hub", "axle_hole", 0), note="rear axle"),
        Placement("axle", 0, 0, 0, rot=90, seat=0.0, tag="front_axle",
                  mount_on=("front_hub", "axle_hole", 0), note="front axle"),
        # Wheels on the axle ends, seated INBOARD so the axle runs through them.
        Placement("wheel", 0, 0, 0, seat=-1.0,
                  mount_on=("rear_axle", "axle", 0), note="rear left wheel"),
        Placement("wheel", 0, 0, 0, seat=-1.0,
                  mount_on=("rear_axle", "axle", 1), note="rear right wheel"),
        Placement("wheel", 0, 0, 0, seat=-1.0,
                  mount_on=("front_axle", "axle", 0), note="front left wheel"),
        Placement("wheel", 0, 0, 0, seat=-1.0,
                  mount_on=("front_axle", "axle", 1), note="front right wheel"),
        Placement("chassis", x=0, z=0, level=chassis_level, note="chassis"),
    ]
    return out + _tile_rect(pool, w=6, d=2, level=body_level,
                            accept=is_wall_brick, note="body")


def _wheel_at_least(ldu: float):
    def pred(c: Candidate) -> bool:
        return c.name.startswith("Wheel") and max(c.geom.size_ldu) >= ldu
    return pred


def _axle_length(lo: float, hi: float):
    def pred(c: Candidate) -> bool:
        if not re.match(r"^Technic Axle \d+$", c.name):
            return False
        return lo <= max(c.geom.size_ldu) <= hi
    return pred


AXLE_CAR = Template(
    name="axle_car",
    title="MOCForge axle-mounted car",
    roles={
        # A brick carrying an axle hole, of any kind - matched by measured
        # function rather than by name, as with the pin-based holder.
        "axle_brick": Role("axle_brick",
                           lambda c: c.geom.count("axle_hole") >= 1
                           and c.name.startswith(("Technic Brick", "Brick")),
                           "brick with an axle hole"),
        # Shortest usable axle: a 10-stud axle on a 2-wide body gives an absurd
        # track, so prefer the smallest that still carries two wheels.
        # Minimum 100 LDU. A 3L axle pin (60 LDU) through a 1-stud brick leaves
        # only 20 LDU of stub each side, which a 40 LDU wide wheel cannot clear
        # without intersecting the brick. Refusing here is deliberate: a clean
        # "no suitable axle" beats emitting a model that will not go together.
        "axle":      Role("axle", _axle_length(100, 200), "Technic axle 5-10"),
        "wheel":     Role("wheel",
                          lambda c: c.name.startswith("Wheel")
                          and max(c.geom.size_ldu) >= 30,
                          "wheel at least 30 LDU across", prefer_largest=True),
        "chassis":   Role("chassis", _plate_between((2, 6), (2, 6)), "Plate 2x6"),
    },
    counts={"axle_brick": 2, "axle": 2, "wheel": 4, "chassis": 1},
    layout=_axle_car_layout,
)

def _technic_layout(a: dict[str, Candidate], pool: list[Candidate],
                    params: dict | None = None) -> list[Placement]:
    """A Technic chassis: one beam spine, two cross axles, four wheels.

    Technic has no stud lattice to build on - 42151-1 carries 467 pieces with
    axle holes and only 85 with studs - so the frame is defined by pin/axle
    couplings instead. A single beam spine keeps the topology honest and
    minimal: axles pass through two of its pin holes and wheels ride the ends.

    The spine sits at wheel-centre height, so with the beam's rotated thickness
    h its underside goes at level (radius - h/2) / 8.
    """
    beam = a["spine"]
    front, rear = a["wheel_front"], a["wheel_rear"]

    def _radius(c: Candidate) -> float:
        return max(c.geom.max_xyz[0] - c.geom.min_xyz[0],
                   c.geom.max_xyz[1] - c.geom.min_xyz[1]) / 2

    # One spine at one height cannot suit two different radii. Use the mean and
    # let the ground-clearance warning report the residual - this is exactly the
    # tolerated Technic error, made visible rather than hidden.
    radius = (_radius(front) + _radius(rear)) / 2

    # After the "rail" rotation the beam's old X extent becomes its height.
    h = beam.geom.max_xyz[0] - beam.geom.min_xyz[0]
    level = max(0.0, (radius - h / 2) / LDU_PER_PLATE)

    holes = [c for c in beam.geom.connections if c.kind == "pin_hole"]
    holes.sort(key=lambda c: c.z)
    if len(holes) < 4:
        return []
    # Axles near each end, one hole in from the tip for a little material.
    rear_i, front_i = 1, len(holes) - 2

    return [
        Placement("spine", x=0, z=0, level=level, rot="rail",
                  tag="spine", note="chassis spine"),
        Placement("cross_axle", 0, 0, 0, rot=90, seat=0.0, tag="rear_axle",
                  mount_on=("spine", "pin_hole", rear_i), note="rear axle"),
        Placement("cross_axle", 0, 0, 0, rot=90, seat=0.0, tag="front_axle",
                  mount_on=("spine", "pin_hole", front_i), note="front axle"),
        Placement("wheel_rear", 0, 0, 0, seat=-1.0,
                  mount_on=("rear_axle", "axle", 0), note="rear left wheel"),
        Placement("wheel_rear", 0, 0, 0, seat=-1.0,
                  mount_on=("rear_axle", "axle", 1), note="rear right wheel"),
        Placement("wheel_front", 0, 0, 0, seat=-1.0,
                  mount_on=("front_axle", "axle", 0), note="front left wheel"),
        Placement("wheel_front", 0, 0, 0, seat=-1.0,
                  mount_on=("front_axle", "axle", 1), note="front right wheel"),
    ]


TECHNIC_CHASSIS = Template(
    name="technic_chassis",
    title="MOCForge Technic chassis",
    roles={
        # Straight beams only. Matching "Technic Beam" plus a hole count picks
        # up L- and T-shapes (32526 is a 3x5 L-shape with 9 holes), which are
        # not rails, and prefer_largest then chose one by area.
        "spine":      Role("spine",
                           lambda c: bool(re.match(r"^Technic Beam 1 x \d+", c.name))
                           and c.geom.count("pin_hole") >= 7,
                           "straight Technic beam, 7+ holes", prefer_largest=True),
        # Longest available: the axle must clear the spine's thickness plus a
        # full wheel width on each side. An Axle 5 (100 LDU) leaves only 50 LDU
        # a side, which a 50 LDU wide rear wheel cannot clear.
        "cross_axle": Role("cross_axle", _axle_length(100, 240),
                           "Technic axle 5-12", prefer_largest=True),
        # Front and rear are separate roles so a staggered setup works. 42151-1
        # carries 2 narrow front and 2 wide rear wheels, never 4 of one kind,
        # which is how the real car is built.
        "wheel_front": Role("wheel_front", _wheel_at_least(30), "front wheel"),
        "wheel_rear":  Role("wheel_rear", _wheel_at_least(30), "rear wheel",
                            prefer_largest=True),
    },
    counts={"spine": 1, "cross_axle": 2, "wheel_front": 2, "wheel_rear": 2},
    layout=_technic_layout,
    studded=False,
)

def pick_palette(pool: list[Candidate], accept: Callable[[Candidate], bool],
                 size: int = 3, budget: Budget | None = None) -> frozenset[int]:
    """The most abundant colours, by stud area, among usable material.

    Chosen by area rather than piece count so a few large plates outrank a
    scattering of 1x1s. Pass a `budget` to rank by what is still UNSPENT, which
    is what makes per-course banding work: each course claims the colour with
    the most material left, so courses come out in blocks instead of noise.

    A single-colour model is usually impossible and that is the inventory's
    fault, not the tiler's - a creative brick box deliberately stocks ~20
    colours at ~15 bricks each, so a 130-piece monochrome wall cannot be built
    from one. Banding is the honest best available.
    """
    area: dict[int, int] = {}
    for c in pool:
        if not accept(c):
            continue
        fw, fd = c.footprint
        if abs(fw - round(fw)) > 0.2 or abs(fd - round(fd)) > 0.2:
            continue
        have = (budget.left.get((c.part_num, c.color_id), 0)
                if budget is not None else c.quantity)
        if have <= 0:
            continue
        area[c.color_id] = area.get(c.color_id, 0) +             int(round(fw)) * int(round(fd)) * have
    top = sorted(area, key=lambda k: -area[k])[:size]
    return frozenset(top)


def _covered(placements: list[Placement]) -> set[tuple[int, int]]:
    """Lattice cells a set of tiled placements actually occupies.

    The nominal outline is not good enough for deciding what a roof may rest
    on: if brick runs out part-way through the top course, some wall cells are
    empty, and a roof plate touching one of those floats.
    """
    out: set[tuple[int, int]] = set()
    for pl in placements:
        if pl.part is None:
            continue
        fw, fd = pl.part.footprint
        pw, pd = int(round(fw)), int(round(fd))
        if pl.rot in (90, 270):
            pw, pd = pd, pw
        for i in range(max(pw, 1)):
            for j in range(max(pd, 1)):
                out.add((int(pl.x) + i, int(pl.z) + j))
    return out


def _area_of(pool: list[Candidate], accept: Callable[[Candidate], bool]) -> int:
    """Total rectangular stud area available in a class of parts."""
    total = 0
    for c in pool:
        if not accept(c):
            continue
        fw, fd = c.footprint
        if abs(fw - round(fw)) > 0.2 or abs(fd - round(fd)) > 0.2:
            continue
        total += int(round(fw)) * int(round(fd)) * c.quantity
    return total


_FAM_CACHE: dict[str, str] = {}


def family_of(c: Candidate) -> str:
    if c.part_num not in _FAM_CACHE:
        _FAM_CACHE[c.part_num] = classify(c.part_num, c.name, c.geom).family
    return _FAM_CACHE[c.part_num]


# Accepted material, defined by taxonomy family plus measured height rather
# than by name. Matching "^Brick N x M$" covers only 22 distinct parts; adding
# the modified variants ("Brick 1 x 2 with Groove" and friends) brings in
# another 603 parts and 63,392 piece occurrences. The height filter is what
# keeps a course uniform, since levels advance by a fixed amount.
WALL_FAMILIES = frozenset({"brick", "brick_modified"})
FLAT_FAMILIES = frozenset({"plate", "plate_modified", "tile"})


def is_wall_brick(c: Candidate) -> bool:
    _w, h, _d = c.geom.size_studs
    return family_of(c) in WALL_FAMILIES and 2.8 <= h <= 3.2


def is_flat_plate(c: Candidate) -> bool:
    _w, h, _d = c.geom.size_studs
    return family_of(c) in FLAT_FAMILIES and 0.8 <= h <= 1.2


def is_roof_plate(c: Candidate) -> bool:
    """Flat plate short enough to bridge a room without sagging.

    A Plate 8 x 16 laid across a building spans 16 studs on a single plate's
    thickness. It bears on both walls, so it is not a cantilever, but it will
    bow and pop off - the stability pass flagged exactly this. Capping the span
    here prevents the design rather than merely reporting it.
    """
    if not is_flat_plate(c):
        return False
    w, _h, d = c.geom.size_studs
    return max(w, d) <= MAX_SPAN


# A model whose height is a small fraction of its width reads as a tray, not a
# building - and no numeric gate catches it. Rendering the output made it
# obvious: a 3-course wall 14 studs wide is 72 LDU tall against 280 LDU wide.
# Footprint is therefore capped by the height the design will actually reach,
# and material availability may only shrink it further.
MIN_ASPECT = 0.55   # height / max horizontal extent


def _aspect_cap(height_ldu: float, aspect: float = MIN_ASPECT) -> int:
    """Widest footprint, in studs, that keeps height/width above `aspect`."""
    return max(3, int(height_ldu / (aspect * LDU_PER_STUD)))


def _solve_building(brick_area: int, ratio: float, scale: float,
                    aspect: float, max_courses: int) -> tuple[int, int, int]:
    """Solve footprint AND height together against the material budget.

    Capping the footprint by a fixed course count was the wrong lever: it kept
    the proportions honest but shrank a 131-piece cottage to 21 pieces. The
    height is the free variable, so for each candidate footprint (largest
    first) this works out how many courses the aspect target demands and
    accepts the footprint only if the brick budget covers that many.

    Returns (w, d, courses); (0, 0, 0) when nothing fits.
    """
    usable = brick_area * scale
    for w in range(40, 2, -1):
        d = max(3, int(round(w * ratio)))
        courses = max(2, math.ceil(
            aspect * max(w, d) * LDU_PER_STUD / LDU_PER_BRICK))
        # A footprint this style cannot carry to a sane height is rejected,
        # not built short. Clamping the height instead was what produced a
        # 27-stud wall only 3 courses tall - aspect 0.15, a tray.
        if courses > max_courses:
            continue
        cost = (2 * w + 2 * d - 4) * courses
        if cost <= usable:
            return (w, d, courses)
    return (0, 0, 0)


def _fit_footprint(brick_area: int, courses: int, ratio: float,
                   scale: float, cap: int | None = None) -> tuple[int, int]:
    """Largest w x d footprint whose walls fit the available brick area.

    A ring course costs 2w + 2d - 4 studs, so `courses` of wall cost
    (2w + 2d - 4) * courses. Sizing has to be derived rather than fixed: across
    the catalogue the structural material available spans 307 to 13,289 studs,
    a 40x range, so no single hardcoded footprint can serve more than a sliver.
    """
    usable = brick_area * scale
    limit = cap if cap is not None else 40
    best = (0, 0)
    for w in range(3, min(limit, 40) + 1):
        d = max(3, int(round(w * ratio)))
        cost = (2 * w + 2 * d - 4) * courses
        if cost <= usable:
            best = (w, d)
        else:
            break
    return best


def _building_layout(a: dict[str, Candidate], pool: list[Candidate],
                     params: dict | None = None) -> list[Placement]:
    """A hollow building: optional floor, N courses of wall, optional roof.

    No fixed roles at all - every part comes from the tiler - which is exactly
    why this reaches far more inventories than the vehicle archetypes. Those
    need four wheels and a mount (36.3% of sets); this needs only rectangular
    bricks and plates (87.0%).
    """
    p = params or {}
    max_courses = int(p.get("courses", 3))
    ratio = float(p.get("ratio", 0.75))
    scale = float(p.get("scale", 0.45))
    aspect = float(p.get("aspect", MIN_ASPECT))

    brick_area = _area_of(pool, is_wall_brick)
    plate_area = _area_of(pool, is_roof_plate)
    w, d, courses = _solve_building(brick_area, ratio, scale, aspect,
                                    max_courses)
    if w < 3 or d < 3:
        return []

    budget = Budget(pool)
    ring = ring_cells(w, d)
    solid = solid_cells(w, d)
    out: list[Placement] = []

    # Whether there is a floor is decided up front, because it shifts the wall
    # base by one plate - but its PARTS are allocated last. The roof has to
    # span from wall to wall, so it needs the large plates; allocating the
    # floor first spent them and left the roof with 1x2 offcuts.
    has_floor = plate_area >= w * d * 2
    level = 1.0 if has_floor else 0.0

    laid = 0
    top_cells: set[tuple[int, int]] = set()
    for k in range(courses):
        course = _tile_cells(pool, ring, level, is_wall_brick,
                             f"wall course {k + 1}", budget, shift=k * 3,
                             palette=pick_palette(pool, is_wall_brick, 1, budget))
        if len(course) < 2:
            break          # ran out of brick; stop cleanly rather than half-build
        out += course
        top_cells = _covered(course)
        level += 3.0       # a brick is 3 plates tall
        laid += 1
    if laid == 0:
        return []

    # Roof gets first claim on the plates, only where it bears on laid brick.
    if p.get("roof", True) and top_cells:
        roof = _tile_cells(pool, solid, level, is_roof_plate, "roof", budget,
                           must_touch=top_cells, span_touch=True,
                           palette=pick_palette(pool, is_roof_plate, 1, budget))
        # No gap-filling second pass: measured across 89 buildings from 50
        # sampled sets, the spanning course left a hole zero times, because the
        # tiler falls back to small plates that always bear on the wall line.
        # Speculative repair code that never executes is a liability.
        out += roof
    # Floor last, from whatever plates remain. It sits on the ground, so it is
    # always supported and never the constrained element.
    if has_floor:
        out += _tile_cells(pool, solid, 0.0, is_flat_plate, "floor", budget,
                           palette=pick_palette(pool, is_flat_plate, 1, budget))
    return out


BUILDING = Template(
    name="building",
    title="MOCForge brick building",
    roles={},          # entirely tiler-driven; nothing is demanded by name
    counts={},
    layout=_building_layout,
    variants=(
        # aspect is the proportion the design is held to; courses is only a
        # practical ceiling on height, not the target.
        {"name": "cottage", "courses": 20, "ratio": 0.75, "scale": 0.45,
         "aspect": 0.60},
        {"name": "tower",   "courses": 24, "ratio": 1.00, "scale": 0.40,
         "aspect": 1.30},
        {"name": "hall",    "courses": 16, "ratio": 0.45, "scale": 0.60,
         "aspect": 0.40},
    ),
)

def inset_cells(w: int, d: int, k: int, mode: str) -> set[tuple[int, int]]:
    """A rectangle inset by k, either on all sides or only two.

    "all" shrinks symmetrically, giving a stepped pyramid. "two" shrinks on the
    +x and +z sides only, giving an asymmetric terrace.
    """
    if mode == "two":
        return {(x, z) for x in range(w - k) for z in range(d - k)}
    return {(x, z) for x in range(k, w - k) for z in range(k, d - k)}


def _solve_stack(area: int, ratio: float, scale: float, mode: str,
                 aspect: float, step_plates: float,
                 max_layers: int) -> tuple[int, int, int]:
    """Solve footprint AND layer count together, as _solve_building does.

    `aspect <= 0` means the design is deliberately flat (a mosaic), in which
    case one layer is correct and the footprint is limited only by material.
    """
    usable = area * scale
    for w in range(48, 3, -1):
        d = max(4, int(round(w * ratio)))
        if aspect <= 0:
            layers = 1
        else:
            layers = max(2, math.ceil(
                aspect * max(w, d) * LDU_PER_STUD / (step_plates * LDU_PER_PLATE)))
            if layers > max_layers:
                continue
        cost = sum(len(inset_cells(w, d, k, mode)) for k in range(layers))
        if cost <= usable:
            return (w, d, layers)
    return (0, 0, 0)


def _fit_layers(area: int, layers: int, ratio: float, scale: float,
                mode: str, cap: int) -> tuple[int, int]:
    """Largest base footprint whose whole stack fits the available area.

    `cap` carries the aspect governance. Without it a six-layer plate stack
    sized only by material came out 13 studs across and 48 LDU tall - a slab
    with faint grooves rather than anything stepped. Rendering it was the only
    way that became visible.
    """
    usable = area * scale
    best = (0, 0)
    for w in range(4, min(cap, 48) + 1):
        d = max(4, int(round(w * ratio)))
        cost = sum(len(inset_cells(w, d, k, mode)) for k in range(layers))
        if cost <= usable:
            best = (w, d)
        else:
            break
    return best


def _sculpture_layout(a: dict[str, Candidate], pool: list[Candidate],
                      params: dict | None = None) -> list[Placement]:
    """A stepped plate sculpture - ziggurat, terrace or flat mosaic.

    Exists to reach the inventories the building archetype cannot: its walls
    need brick-height material, and the largest remaining group of unserved
    sets is dominated by plates and tiles. This archetype uses no bricks at all.

    Every layer is nested inside the one below, so each part always rests on
    solid material - the structure is valid by construction rather than by
    inspection.
    """
    p = params or {}
    max_layers = int(p.get("layers", 5))
    ratio = float(p.get("ratio", 1.0))
    scale = float(p.get("scale", 0.55))
    mode = str(p.get("mode", "all"))
    aspect = float(p.get("aspect", MIN_ASPECT))

    # Layer material. Bricks are 3 plates tall, so a stack of them actually
    # steps; a stack of plates is 8 LDU per layer and needs a far smaller
    # footprint to read as anything other than a slab. Bricks are preferred
    # when there are enough, and plates are the fallback that keeps this
    # archetype reaching the plate-heavy inventories it exists for.
    brick_area = _area_of(pool, is_wall_brick)
    plate_area = _area_of(pool, is_flat_plate)
    nominal = sum(len(inset_cells(8, 8, k, mode)) for k in range(max_layers))
    if brick_area * scale >= nominal:
        material, step_plates = is_wall_brick, 3.0
        area = brick_area
    else:
        material, step_plates = is_flat_plate, 1.0
        area = plate_area

    w, d, layers = _solve_stack(area, ratio, scale, mode, aspect,
                                step_plates, max_layers)
    if w < 4 or d < 4:
        return []

    budget = Budget(pool)
    out: list[Placement] = []
    below: set[tuple[int, int]] | None = None
    for k in range(layers):
        cells = inset_cells(w, d, k, mode)
        if len(cells) < 2:
            break
        # Each layer must rest on what the previous layer actually laid, not on
        # its planned outline - a layer can come up short when material runs out.
        # A tight per-layer palette gives banded courses rather than noise.
        layer = _tile_cells(pool, cells, k * step_plates, material,
                            f"layer {k + 1}", budget, shift=k * 2,
                            must_touch=below,
                            palette=pick_palette(pool, material, 1, budget))
        if not layer:
            break
        out += layer
        below = _covered(layer)
    return out


SCULPTURE = Template(
    name="sculpture",
    title="MOCForge stepped plate sculpture",
    roles={},
    counts={},
    layout=_sculpture_layout,
    variants=(
        {"name": "ziggurat", "layers": 20, "ratio": 1.00, "scale": 0.55,
         "mode": "all", "aspect": 0.70},
        {"name": "terrace",  "layers": 16, "ratio": 0.70, "scale": 0.55,
         "mode": "two", "aspect": 0.50},
        {"name": "mosaic",   "layers": 1, "ratio": 1.00, "scale": 0.70,
         "mode": "all", "aspect": 0.0},   # deliberately flat
    ),
)

def _straight_beam(min_holes: int):
    def pred(c: Candidate) -> bool:
        return (bool(re.match(r"^Technic Beam 1 x \d+", c.name))
                and c.geom.count("pin_hole") >= min_holes)
    return pred


def _pin_spanning(courses: int = 2):
    """A Technic pin long enough to join `courses` stacked beams.

    A beam is 20 LDU tall, so a two-course joint needs a 40 LDU pin - which is
    exactly the length of 61332, the commonest Technic pin in existence. Longer
    pins are rejected rather than tolerated: a 60 LDU pin through a two-course
    joint protrudes 20 LDU into open air.
    """
    want = courses * 20.0
    def pred(c: Candidate) -> bool:
        if not c.name.startswith("Technic Pin"):
            return False
        # "Technic Pin Connector Hub with 1 Pin" also starts with Technic Pin
        # and is about the right length, but it is a bulky hub that fouls the
        # beam it is meant to join. Require a plain pin, slim in section.
        if re.search(r"Connector|Hub|Toggle|Joint|Tow Ball", c.name, re.I):
            return False
        size = sorted(c.geom.size_ldu)
        return abs(size[2] - want) < 3.0 and size[1] <= 17.0
    return pred


def _crib_layout(a: dict[str, Candidate], pool: list[Candidate],
                 params: dict | None = None) -> list[Placement]:
    """A Technic crib: alternating courses of beams, pinned at the corners.

    This exists because a Technic collection was being served almost nothing.
    A set like 42151-1 carries 1,236 beams and 2,476 pins across a few sets and
    the only Technic archetype used seven pieces, because the vehicle
    archetypes need studded plates and wheel mounts that Technic sets lack.

    Laid flat, a beam's pin holes point vertically - which is what makes this
    work. Courses alternate direction, so each course rests crosswise on the one
    below and is held by pins through the coincident holes at the corners. The
    structure is self-bracing, which is exactly why real Technic models are
    built this way.
    """
    p = params or {}
    beam, pin = a["beam"], a["pin"]
    max_courses = int(p.get("courses", 10))

    # Canonical beam: depth x height x length. Holes sit on the top face.
    depth, height, length = beam.geom.size_ldu
    side = length / LDU_PER_STUD          # studs
    thick = depth / LDU_PER_STUD
    course_plates = height / LDU_PER_PLATE

    holes = sorted((c for c in beam.geom.connections if c.kind == "pin_hole"),
                   key=lambda c: c.z)
    if len(holes) < 3 or side < 3:
        return []

    # Height is bounded by BOTH stocks, not just the beams. Two beams per
    # course, and four corner pins per joint over (courses - 1) joints. Sizing
    # on beams alone produced a 12-course crib calling for 44 pins from a
    # stock of 8 - a model the user could not build, which is the one failure
    # this product cannot ship.
    by_beam = beam.quantity // 2
    by_pin = pin.quantity // 4 + 1
    courses = max(2, min(max_courses, by_beam, by_pin))
    if courses < 2 or pin.quantity < 4:
        return []

    out: list[Placement] = []
    for k in range(courses):
        level = k * course_plates
        along_x = (k % 2 == 0)
        for side_index in (0, 1):
            offset = 0.0 if side_index == 0 else side - thick
            tag = f"c{k}s{side_index}"
            if along_x:
                out.append(Placement("beam", x=0.0, z=offset, level=level,
                                     rot=90, tag=tag,
                                     note=f"course {k + 1} beam"))
            else:
                out.append(Placement("beam", x=offset, z=0.0, level=level,
                                     rot=0, tag=tag,
                                     note=f"course {k + 1} beam"))
        # Pins bridge this course to the one above, one at each corner where
        # the crossing course lands.
        #
        # Placed by level rather than mounted on a hole. A pin spanning a joint
        # has a known extent - 40 LDU, exactly two 20 LDU courses - so its
        # position is fully determined: bottom at the bottom of this course,
        # top at the top of the next. Mounting it on the hole instead made the
        # position depend on where LDraw happens to put that hole's reference
        # point, which is not consistent between a hole modelled with one
        # primitive and one modelled as two rims, and left the bottom course's
        # pins protruding below the model's own ground plane.
        if k + 1 < courses:
            pw, _ph, pd = (v / LDU_PER_STUD for v in
                           _rotate_bbox(pin.geom.min_xyz, pin.geom.max_xyz, "z90")[1])
            pin_w = (pin.geom.max_xyz[1] - pin.geom.min_xyz[1]) / LDU_PER_STUD
            pin_d = (pin.geom.max_xyz[2] - pin.geom.min_xyz[2]) / LDU_PER_STUD
            for cx in (0.0, side - thick):
                for cz in (0.0, side - thick):
                    out.append(Placement(
                        "pin",
                        x=cx + (thick - pin_w) / 2,
                        z=cz + (thick - pin_d) / 2,
                        level=level, rot="z90",
                        note=f"course {k + 1} corner pin"))
    return out


TECHNIC_CRIB = Template(
    name="technic_crib",
    title="MOCForge Technic crib tower",
    roles={
        # Ranked by quantity rather than length: a crib needs two matching
        # beams per course, so how many of one length exist is what decides how
        # tall it can be.
        "beam": Role("beam", _straight_beam(5),
                     "straight Technic beam, 5+ holes", prefer_stock=True),
        # Also ranked by stock: the crib's height is capped by four pins per
        # joint, so picking the pin there are most of is what lets it grow.
        "pin":  Role("pin", _pin_spanning(2), "Technic pin, 2 studs long",
                     prefer_stock=True),
    },
    counts={"beam": 4, "pin": 4},
    layout=_crib_layout,
    studded=False,
    variants=(
        {"name": "tower", "courses": 12},
        {"name": "plinth", "courses": 4},
    ),
)

def _frame_layout(a: dict[str, Candidate], pool: list[Candidate],
                  params: dict | None = None) -> list[Placement]:
    """A Technic ladder frame: two rails in Z with cross-rungs spanning in X.

    The crib needs many copies of ONE beam length.  The frame uses ALL available
    straight beams by cycling through every valid type as rungs, so a Technic set
    with 121 beams across 25 types fills far more slots than the crib's 14-piece
    ceiling.

    Physical model: rails lie at level=0; rungs lie on TOP of the rails at
    level=rail_height (one beam-height higher). This gives zero Y-overlap between
    rails and rungs — the collision detector skips them — while the bridge test
    confirms the rungs are physically supported across their span.

    Rungs longer than MAX_SPAN studs are excluded from 'wide' mode because they
    would fail the bridge test even when both ends rest on rails.

    'compact': one rung type (most-stocked) — fastest to build.
    'wide'   : cycles through ALL valid rung types per slot — maximum material use.
    """
    p = params or {}
    wide = p.get("name") != "compact"

    is_beam = _straight_beam(3)
    all_beams = [c for c in pool if is_beam(c)]
    all_pins  = [c for c in pool if _pin_spanning(2)(c)]
    if not all_beams or not all_pins:
        return []

    budget = Budget(pool)
    pin = max(all_pins, key=lambda c: c.quantity)

    # Rail: longest straight beam with ≥2 copies (determines frame depth).
    rail: Candidate | None = None
    for b in sorted(all_beams, key=lambda c: (-c.geom.size_ldu[2], -c.quantity)):
        if budget.left.get((b.part_num, b.color_id), 0) >= 2:
            rail = b
            break
    if rail is None:
        return []

    rail_depth_ldu, rail_height_ldu, _ = rail.geom.size_ldu
    rail_thick     = rail_depth_ldu  / LDU_PER_STUD
    rung_level     = rail_height_ldu / LDU_PER_PLATE   # rungs sit on top of rails

    # Holes are in the rail's LOCAL frame. Convert to global Z using the same
    # offset the Fitter applies: for a beam placed at pl.z=0, off_z = -min_xyz[2].
    # A 15-hole beam centred at its origin has min_z=-149, so its first hole at
    # local Z=-140 lands at global Z = -140 - (-149) = 9 LDU, not -140 LDU.
    rail_z_offset = -rail.geom.min_xyz[2]
    holes = sorted(
        c.z + rail_z_offset
        for c in rail.geom.connections if c.kind == "pin_hole"
    )
    if len(holes) < 2:
        return []

    def _rung_valid(b: Candidate) -> bool:
        """A rung must bridge both rails without exceeding the span limit."""
        return (b.geom.size_ldu[2] / LDU_PER_STUD) <= MAX_SPAN + 0.5

    if wide:
        # Frame width = shortest valid rung (so ALL valid rungs reach both rails).
        min_rung_ldu: float | None = None
        for b in sorted(all_beams, key=lambda c: c.geom.size_ldu[2]):
            if b.part_num == rail.part_num:
                continue
            if budget.left.get((b.part_num, b.color_id), 0) < 1:
                continue
            if _rung_valid(b):
                min_rung_ldu = b.geom.size_ldu[2]
                break
        if min_rung_ldu is None:
            surplus = budget.left.get((rail.part_num, rail.color_id), 0) - 2
            if surplus < 1 or not _rung_valid(rail):
                return []
            min_rung_ldu = rail.geom.size_ldu[2]

        frame_width = min_rung_ldu / LDU_PER_STUD
        rung_queue: list[Candidate] = sorted(
            (b for b in all_beams
             if b.part_num != rail.part_num
             and b.geom.size_ldu[2] >= min_rung_ldu - 2     # must reach both rails
             and _rung_valid(b)
             and budget.left.get((b.part_num, b.color_id), 0) >= 1),
            key=lambda c: (-c.quantity, -c.geom.size_ldu[2]),
        )
        rung_idx = 0
    else:
        # 'compact': single most-stocked valid rung type.
        compact_rung: Candidate | None = None
        for b in sorted(all_beams, key=lambda c: (-c.quantity, -c.geom.size_ldu[2])):
            if b.part_num == rail.part_num:
                continue
            if budget.left.get((b.part_num, b.color_id), 0) < 1:
                continue
            if _rung_valid(b):
                compact_rung = b
                break
        if compact_rung is None:
            surplus = budget.left.get((rail.part_num, rail.color_id), 0) - 2
            if surplus < 1 or not _rung_valid(rail):
                return []
            compact_rung = rail
        frame_width = compact_rung.geom.size_ldu[2] / LDU_PER_STUD
        rung_queue = [compact_rung]
        rung_idx = 0

    out: list[Placement] = []
    for rail_x in (0.0, frame_width - rail_thick):
        if not budget.take(rail):
            break
        out.append(Placement(role="_tiled", x=rail_x, z=0.0, level=0.0,
                             rot=0, part=rail, note="rail"))
    if len(out) < 2:
        return []

    for hz_ldu in holes:
        hz_studs = hz_ldu / LDU_PER_STUD

        if wide:
            placed = False
            for i in range(len(rung_queue)):
                ri = (rung_idx + i) % len(rung_queue)
                rung = rung_queue[ri]
                if budget.take(rung):
                    rung_idx = (ri + 1) % len(rung_queue)
                    placed = True
                    rung_thick = rung.geom.size_ldu[0] / LDU_PER_STUD
                    break
            if not placed:
                break
        else:
            rung = rung_queue[0]
            if not budget.take(rung):
                break
            rung_thick = rung.geom.size_ldu[0] / LDU_PER_STUD

        # Rungs at rung_level (one beam-height above the rails) — this places them
        # physically ON TOP of the rails, which eliminates the XY-plane collision
        # that would fire if rails and rungs occupied the same height.
        out.append(Placement(role="_tiled",
                             x=0.0, z=hz_studs - rung_thick / 2,
                             level=rung_level, rot=90,
                             part=rung, note="cross beam"))
        for pin_x in (0.0, frame_width - rail_thick):
            if budget.take(pin):
                out.append(Placement(role="_tiled",
                                     x=pin_x, z=hz_studs,
                                     level=0.0, rot=0,
                                     part=pin, note="junction pin"))

    return out if len(out) >= 4 else []


TECHNIC_FRAME = Template(
    name="technic_frame",
    title="MOCForge Technic ladder frame",
    roles={},   # pool-driven; no pre-assigned roles
    counts={},
    layout=_frame_layout,
    studded=False,
    variants=(
        {"name": "wide"},    # builds as many panels as beam stock allows
        {"name": "compact"}, # single panel — the crib's complement
    ),
)

TEMPLATES = {t.name: t for t in (STUDDED_CAR, AXLE_CAR, TECHNIC_CHASSIS,
                                 TECHNIC_CRIB, TECHNIC_FRAME,
                                 BUILDING, SCULPTURE)}


# ------------------------------------------------------------------------ main

def main(argv: list[str]) -> int:
    if not argv or argv[0] == "list":
        for name, t in TEMPLATES.items():
            print(f"{name:16s} {t.title}")
            for rn, r in t.roles.items():
                print(f"    {rn:14s} {r.describe}  x{t.demand().get(rn,0)}")
        return 0

    if argv[0] == "selftest":
        # Guards against template drift: a layout that references a role the
        # template never declares. Caught once for real, when a bulk rename of
        # "wheel" to "wheel_front"/"wheel_rear" leaked from the Technic layout
        # into the axle one and silently cost an inventory's coverage.
        cat, lib = Catalogue(), LDrawLibrary()
        fitter = Fitter(cat, lib)
        failures = []
        for name, tpl in TEMPLATES.items():
            declared = set(tpl.roles)
            if set(tpl.counts) - declared:
                failures.append(f"{name}: counts name undeclared roles "
                                f"{sorted(set(tpl.counts) - declared)}")
            # Build a fake assignment so the layout can run without inventory.
            fake = {}
            for rn in declared:
                fake[rn] = None
            refs = set()
            try:
                # Probe with a real inventory so geometry is available.
                lots = cat.set_lots("10696-1")
                b = fitter.fit(tpl, lots)
                refs = {p.role for p in b.parts} | {
                    u.split("role ")[-1].strip("'\" ") for u in b.unfilled
                    if "unassigned" in u}
                bad = {r for r in refs if r not in declared and r != "_tiled"}
                if bad:
                    failures.append(f"{name}: layout references undeclared "
                                    f"role(s) {sorted(bad)}")
            except Exception as e:
                failures.append(f"{name}: layout raised {type(e).__name__}: {e}")
            print(f"  {'ok  ' if not failures or name not in failures[-1] else 'FAIL'} "
                  f"{name:16s} roles={len(declared)} counts={sum(tpl.counts.values())}")
        print()

        # The product's central promise: a design must be buildable from the
        # declared inventory alone. A budget bug here would hand the user a
        # model requiring parts they do not own - the single failure that would
        # make the whole product worthless - so it is a permanent gate, not a
        # spot check. Audited independently across 325 designs at 0 violations
        # before being pinned here.
        # Probes: fixed fixtures for reproducibility PLUS a seeded random
        # sample. The fixed five alone missed a real violation in the Technic
        # crib, which only appears when pin stock is low relative to beam
        # stock - a shape no hand-picked fixture happened to have.
        import random as _random
        _rows = [r[0] for r in cat.conn.execute(
            "SELECT set_num FROM sets WHERE num_parts >= 100 ORDER BY set_num")]
        _rng = _random.Random(1729)
        probes = ["10696-1", "42151-1", "11004-1", "19011-1", "10715-1"]
        probes += _rng.sample(_rows, 25)
        # A multi-set inventory too: combining sets is where stock ratios shift.
        probes.append(("42205-1", "42232-1", "42151-1"))

        audited = over = 0
        for probe in probes:
            probe_sets = probe if isinstance(probe, tuple) else (probe,)
            try:
                probe_lots = cat.combine(*probe_sets)
            except KeyError:
                continue
            for tname, t in TEMPLATES.items():
                for variant in t.variants:
                    rb = fitter.fit(t, probe_lots, variant)
                    if not rb.buildable:
                        continue
                    audited += 1
                    tally: dict[tuple[str, int], int] = {}
                    for part in rb.parts:
                        k = (part.part_num, part.color_id)
                        tally[k] = tally.get(k, 0) + 1
                    for k, n in tally.items():
                        if n > probe_lots.get(k, 0):
                            over += 1
                            failures.append(
                                f"{tname}/{variant.get('name')} on "
                                f"{'+'.join(probe_sets)} uses {n} x {k} but only "
                                f"{probe_lots.get(k, 0)} owned")
        print(f"  inventory-subset audit: {audited} designs, "
              f"{over} exceeding owned quantities")
        if failures:
            print("GATE FAILED:")
            for f in failures:
                print(f"  - {f}")
            return 1
        print(f"GATE PASSED: {len(TEMPLATES)} templates consistent.")
        return 0

    if argv[0] == "suggest":
        # The product behaviour: try every archetype against the inventory and
        # return the ones that actually work, best first. Anything that fails
        # validation is withheld, never shown.
        sets = argv[1:]
        cat, lib = Catalogue(), LDrawLibrary()
        lots = cat.combine(*sets)
        fitter = Fitter(cat, lib)
        print(f"inventory: {' + '.join(sets)} = {sum(lots.values())} pieces "
              f"/ {len(lots)} lots\n")
        results = []
        for name, tpl in TEMPLATES.items():
            for variant in tpl.variants:
                build = fitter.fit(tpl, lots, variant)
                results.append((build, tpl, variant))
        results.sort(key=lambda r: (-r[0].confidence, r[0].template))
        offered = 0
        for build, tpl, variant in results:
            vn = variant.get("name", "")
            slug = f"{tpl.name}{'-' + vn if vn else ''}"
            if build.buildable:
                offered += 1
                out = fitter.write_ldr(
                    build, OUT_DIR / f"{slug}_{'_'.join(sets)}.ldr", tpl.title)
                print(f"  OFFER  {slug:22s} conf {build.confidence:.3f}  "
                      f"{build.piece_count:3d} pieces  -> {out.name}")
            else:
                why = (build.unfilled or build.collisions or build.unsupported
                       or ([f"only {build.piece_count} pieces; minimum {MIN_PIECES}"]
                           if build.piece_count < MIN_PIECES else []))[:1]
                reason = why[0] if why else "produced no placements"
                print(f"  hold   {slug:22s} conf {build.confidence:.3f}  "
                      f"withheld: {reason[:48]}")
        print(f"\n{offered} of {len(results)} archetypes buildable from this inventory")
        return 0 if offered else 1

    if argv[0] == "build":
        tpl_name, sets = argv[1], argv[2:]
        if tpl_name not in TEMPLATES:
            raise SystemExit(f"unknown template {tpl_name!r}; try 'list'")
        cat, lib = Catalogue(), LDrawLibrary()
        lots = cat.combine(*sets)
        print(f"inventory: {' + '.join(sets)} = {sum(lots.values())} pieces / {len(lots)} lots\n")
        fitter = Fitter(cat, lib)
        tpl = TEMPLATES[tpl_name]
        build = fitter.fit(tpl, lots, tpl.variants[0])
        print(build.report())
        if build.assignments:
            print("\nrole assignments:")
            for role, c in build.assignments.items():
                w, d = c.footprint
                print(f"    {role:14s} -> {c.part_num:>10} {c.name[:40]:<40} "
                      f"({w:g}x{d:g} studs, {c.quantity} owned)")
        if build.parts:
            out = fitter.write_ldr(
                build, OUT_DIR / f"{tpl_name}_{'_'.join(sets)}.ldr",
                TEMPLATES[tpl_name].title)
            print(f"\nwrote {out}")
            print("\n" + fitter.instructions(build))
        return 0 if build.buildable else 1

    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
