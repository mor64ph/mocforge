"""MOCForge - isometric rendering of builds and build steps.

Instructions were text-only, which is the largest gap between this and
something you would actually hand to someone. Every part's position, size and
colour is already known exactly, so no 3D engine is needed: each part is an
axis-aligned box, and an axis-aligned box in isometric projection is three
quadrilaterals.

Projection. LDraw has +Y pointing DOWN, so the viewer sits at (+x, -y, +z):

    screen_x = (x - z) * cos(30deg)
    screen_y = (x + z) * sin(30deg) + y

Depth along the view direction is therefore `x - y + z`; parts are painted in
ascending depth so nearer parts land on top (painter's algorithm). This is
exact for non-interpenetrating axis-aligned boxes, which the collision pass
already guarantees.

Usage:
  python render.py build 10696-1 building cottage
  python render.py steps 10696-1 sculpture ziggurat
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from generate import Build, LDrawColours, PlacedPart

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"

COS30 = math.cos(math.radians(30))
SIN30 = math.sin(math.radians(30))

# Face shading. Relative brightness of the three visible faces of a box; the
# top catches the most light, and the two sides differ so edges read clearly
# even between two parts of the same colour.
SHADE_TOP = 1.00
SHADE_LEFT = 0.72
SHADE_RIGHT = 0.86

# Families drawn as cylinders rather than boxes. A wheel's bounding box is a
# cube, so box rendering turned every wheel, tyre, pin and axle into a block -
# fine for the building and sculpture archetypes, badly wrong for vehicles.
ROUND_FAMILIES = frozenset({
    "wheel", "tyre", "technic_pin", "technic_axle", "round_brick", "round_plate",
})

# Points sampled around a circle. 28 is smooth at print resolution without
# making the silhouette hull expensive on a 140-part model.
CIRCLE_STEPS = 28

BACKGROUND = (250, 249, 247)
GHOST = (214, 211, 206)
OUTLINE = (32, 34, 38)


def project(x: float, y: float, z: float) -> tuple[float, float]:
    return ((x - z) * COS30, (x + z) * SIN30 + y)


def _shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(c * factor)))) for c in rgb)


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convex hull, Andrew's monotone chain.

    The outline of a projected cylinder is the hull of both its end circles,
    which saves working out where the silhouette lines are tangent.
    """
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out: list[tuple[float, float]] = []
        for q in seq:
            while len(out) >= 2:
                (x1, y1), (x2, y2) = out[-2], out[-1]
                if (x2 - x1) * (q[1] - y1) - (y2 - y1) * (q[0] - x1) > 0:
                    break
                out.pop()
            out.append(q)
        return out

    return half(pts)[:-1] + half(reversed(pts))[:-1]


def _cylinder_axis(size: tuple[float, float, float]) -> int:
    """Which axis a round part spins about.

    A cylinder measures the same across both diameters and differs along its
    axis, so the odd dimension out is the axis: a wheel at 42 x 42 x 20 LDU
    spins about Z. Taking the smallest would be wrong for a long thin pin,
    which is 40 LDU along its axis and 15 across.
    """
    order = sorted(range(3), key=lambda i: size[i])
    mid = size[order[1]]
    return max(range(3), key=lambda i: abs(size[i] - mid))


class Renderer:
    def __init__(self):
        self.colours = LDrawColours()
        # One lookup across both palettes: the renderer only needs a code's RGB
        # and does not care whether the colour was solid or transparent.
        self._rgb: dict[int, tuple[int, int, int]] = {}
        self._rgb.update(self.colours.solid)
        self._rgb.update(self.colours.trans)

    def rgb_for(self, code: int) -> tuple[int, int, int]:
        return self._rgb.get(code, (150, 150, 150))

    # ------------------------------------------------------------------ faces

    def _faces(self, p: PlacedPart):
        """The three visible faces of a part's box, as projected polygons."""
        (x0, y0, z0), (x1, y1, z1) = p.bbox
        # y0 is the top (smaller y is higher, since +Y is down).
        top = [project(x0, y0, z0), project(x1, y0, z0),
               project(x1, y0, z1), project(x0, y0, z1)]
        # The two faces turned toward the viewer at (+x, -y, +z).
        right = [project(x1, y0, z0), project(x1, y0, z1),
                 project(x1, y1, z1), project(x1, y1, z0)]
        front = [project(x0, y0, z1), project(x1, y0, z1),
                 project(x1, y1, z1), project(x0, y1, z1)]
        return (
            (front, SHADE_LEFT),
            (right, SHADE_RIGHT),
            (top, SHADE_TOP),
        )

    def _cylinder(self, p: PlacedPart):
        """A round part as two end discs plus a silhouette."""
        (x0, y0, z0), (x1, y1, z1) = p.bbox
        size = (x1 - x0, y1 - y0, z1 - z0)
        axis = _cylinder_axis(size)
        centre = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]
        lo = (x0, y0, z0)[axis]
        hi = (x1, y1, z1)[axis]
        # Radius from the two axes that are not the spin axis.
        others = [i for i in range(3) if i != axis]
        radius = min(size[i] for i in others) / 2

        caps = []
        for end in (lo, hi):
            ring = []
            for k in range(CIRCLE_STEPS):
                t = 2 * math.pi * k / CIRCLE_STEPS
                q = list(centre)
                q[axis] = end
                q[others[0]] = centre[others[0]] + radius * math.cos(t)
                q[others[1]] = centre[others[1]] + radius * math.sin(t)
                ring.append(project(*q))
            caps.append((ring, end))

        # Depth grows with x, z and with -y, so the nearer cap is whichever end
        # sits further along that direction.
        sign = -1.0 if axis == 1 else 1.0
        near = max(caps, key=lambda c: c[1] * sign)
        far = min(caps, key=lambda c: c[1] * sign)
        # A cap's facing follows its axis: vertical reads as a lit top face,
        # horizontal as one of the two side faces.
        cap_shade = (SHADE_RIGHT, SHADE_TOP, SHADE_LEFT)[axis]
        return (
            (_hull(near[0] + far[0]), (SHADE_LEFT + SHADE_RIGHT) / 2),
            (near[0], cap_shade),
        )

    @staticmethod
    def _depth(p: PlacedPart) -> float:
        (x0, y0, z0), (x1, y1, z1) = p.bbox
        cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
        return cx - cy + cz

    # ----------------------------------------------------------------- render

    def render(self, parts: list[PlacedPart], path: Path,
               width: int = 1100, height: int = 850,
               ghost_upto: int | None = None,
               fit_parts: list[PlacedPart] | None = None) -> Path:
        """Draw parts isometrically. `ghost_upto` greys the first N parts.

        Ghosting is what makes a step image legible: the new pieces read as
        colour against everything already built, rather than the reader having
        to diff two pictures.

        `fit_parts` fixes the projection to a different set than the one being
        drawn. Step images pass the finished model, so every step shares one
        frame: without it each step was fitted to its own content, so the model
        grew and shifted between pages and a reader had to re-find their place
        on every step. Real instructions hold the viewpoint still.

        Cost is `steps x parts`, because each step redraws everything placed so
        far. The obvious fix - keep the canvas and draw only the new parts onto
        the previous step's image - was tested and is WRONG. It is exact only
        when every new part is nearer than every part already drawn, and depth
        is `x - y + z`: climbing one course adds 20 LDU while a footprint spans
        200+ LDU in x+z, so a part at the back of a new course is always
        further than one at the front of an older course. Measured across three
        archetypes: safe on 0 of 12 step transitions. Painter's order genuinely
        interleaves, so per-step redraw is inherent; cache the results instead.
        """
        if not parts:
            raise ValueError("nothing to render")

        order = sorted(range(len(parts)), key=lambda i: self._depth(parts[i]))

        # Fit the projection to the canvas from the real extents - of
        # `fit_parts` when given, so a sequence of step images can share one
        # frame instead of each being fitted to its own content.
        pts = [project(cx, cy, cz)
               for p in (fit_parts if fit_parts is not None else parts)
               for cx in (p.bbox[0][0], p.bbox[1][0])
               for cy in (p.bbox[0][1], p.bbox[1][1])
               for cz in (p.bbox[0][2], p.bbox[1][2])]
        min_sx = min(q[0] for q in pts)
        max_sx = max(q[0] for q in pts)
        min_sy = min(q[1] for q in pts)
        max_sy = max(q[1] for q in pts)
        pad = 48
        span_x = max(max_sx - min_sx, 1e-6)
        span_y = max(max_sy - min_sy, 1e-6)
        scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
        off_x = (width - span_x * scale) / 2 - min_sx * scale
        off_y = (height - span_y * scale) / 2 - min_sy * scale

        def to_px(pt: tuple[float, float]) -> tuple[float, float]:
            return (pt[0] * scale + off_x, pt[1] * scale + off_y)

        img = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(img)
        # Outline width tracks scale so a small model is not swamped by it.
        lw = max(1, int(round(scale * 2.2)))

        for i in order:
            p = parts[i]
            ghosted = ghost_upto is not None and i < ghost_upto
            base = GHOST if ghosted else self.rgb_for(p.colour)
            polys = (self._cylinder(p) if p.family in ROUND_FAMILIES
                     else self._faces(p))
            for poly, factor in polys:
                draw.polygon([to_px(q) for q in poly],
                             fill=_shade(base, factor),
                             outline=OUTLINE if not ghosted else None,
                             width=lw)

        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        return path

    def render_build(self, build: Build, path: Path, **kw) -> Path:
        return self.render(build.parts, path, **kw)

    def render_steps(self, build: Build, directory: Path,
                     stem: str = "step", **kw) -> list[Path]:
        """One image per build step, each ghosting everything already placed.

        Takes the same `width`/`height` as `render`. Without the passthrough,
        step images were locked to the 1100x850 default - about 180 DPI at the
        width they occupy in a printed instruction page, while the cover model
        could ask for 1600x1200. A printable document should be able to request
        print resolution for the pages a builder actually works from.
        """
        by_level: dict[float, list[PlacedPart]] = {}
        for p in build.parts:
            by_level.setdefault(p.level, []).append(p)

        ordered: list[PlacedPart] = []
        out: list[Path] = []
        for n, level in enumerate(sorted(by_level), start=1):
            already = len(ordered)
            ordered = ordered + by_level[level]
            out.append(self.render(ordered, directory / f"{stem}-{n:02d}.png",
                                   ghost_upto=already,
                                   fit_parts=build.parts, **kw))
        return out


def _fit(set_num: str, archetype: str, variant: str | None):
    from inventory import Catalogue
    from ldraw import LDrawLibrary
    from generate import Fitter, TEMPLATES

    cat, lib = Catalogue(), LDrawLibrary()
    fitter = Fitter(cat, lib)
    tpl = TEMPLATES[archetype]
    params = next((v for v in tpl.variants if v.get("name") == variant),
                  tpl.variants[0])
    build = fitter.fit(tpl, cat.combine(set_num), params)
    return build, params


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    set_num = sys.argv[2] if len(sys.argv) > 2 else "10696-1"
    archetype = sys.argv[3] if len(sys.argv) > 3 else "building"
    variant = sys.argv[4] if len(sys.argv) > 4 else None

    build, params = _fit(set_num, archetype, variant)
    if not build.parts:
        raise SystemExit(f"{archetype}/{variant} yields nothing for {set_num}")
    label = f"{archetype}{'-' + params['name'] if 'name' in params else ''}"
    r = Renderer()

    if cmd == "build":
        p = r.render_build(build, OUT_DIR / f"{label}_{set_num}.png")
        print(f"{p}  ({build.piece_count} parts, conf {build.confidence:.3f})")
    elif cmd == "steps":
        paths = r.render_steps(build, OUT_DIR / f"{label}_{set_num}_steps")
        print(f"{len(paths)} step images in {paths[0].parent}")
        for p in paths:
            print(f"  {p.name}")
    else:
        raise SystemExit(__doc__)
