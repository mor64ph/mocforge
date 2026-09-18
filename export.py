"""MOCForge - packaging one design as a download anyone can open.

The generator's native output is an LDraw `.ldr`, which opens in Stud.io,
LDView or LeoCAD and nowhere else. For the ordinary owner of a brick box that
is nearly the same as producing nothing: the model exists, but they cannot look
at it, let alone build from it. This module turns a design into a single zip
whose centrepiece is a printable PDF, and keeps the `.ldr` inside it for the
minority who do have CAD software.

Two stages, deliberately separate:

  `render_design`  drives `render.Renderer` and is the only part that needs the
                   geometry-bound engine thread, a temporary directory and real
                   seconds.
  `package`        is pure - specification plus PNG bytes in, zip bytes out -
                   so the document layout is testable without a catalogue, an
                   LDraw library or a fitter.

Document format. reportlab writes the PDF because its text stays *text*:
selectable, searchable, and crisp at whatever size it is printed, with a page
of part names costing a couple of kilobytes. PIL can also emit a PDF, but only
as a raster of each page - every part name would ship as pixels, sized for one
guess at the reader's printer.

Reproducibility. A design id is a hash of (archetype, variant, set numbers) and
is stable across restarts, so the package that id names is built to be stable
too: no timestamps anywhere, fixed zip member dates, and reportlab in invariant
mode. The same design therefore always packages to the same bytes, which is
what makes the service's cache verifiable rather than merely plausible.
"""

from __future__ import annotations

import io
import re
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from generate import Build
from render import Renderer

# --------------------------------------------------------------- attribution

# PRD R4/R5. This is a licence and trademark obligation, so it is stated once
# here and reused by every artefact in the package: the PDF cover, every PDF
# page footer and ATTRIBUTION.txt. A downloaded pack outlives the page that
# served it, which is exactly why the notice has to travel inside it.
REBRICKABLE_URL = "https://rebrickable.com/downloads/"
LDRAW_URL = "https://library.ldraw.org/"
CC_BY_URL = "https://creativecommons.org/licenses/by/2.0/"

ATTRIBUTION_LINES = (
    "LEGO catalogue data from Rebrickable. Part geometry from the LDraw Parts",
    "Library, licensed CC BY 2.0. LEGO® is a trademark of the LEGO Group,",
    "which does not sponsor, authorise or endorse this project.",
)
ATTRIBUTION_FOOTER = (
    "Catalogue data: Rebrickable. Geometry: LDraw Parts Library (CC BY 2.0). "
    "LEGO® is a trademark of the LEGO Group, which does not endorse this."
)

# ------------------------------------------------------------------- failures


class ExportError(Exception):
    """Anything that stops a design becoming a package."""


class RenderFailed(ExportError):
    """The isometric renderer could not draw this design.

    Distinct from `PackageFailed` because the causes are unrelated - a build
    the renderer cannot project, against a filesystem that will not hold the
    images - and a caller reporting one as the other sends the reader looking
    in the wrong place.
    """


class PackageFailed(ExportError):
    """The document or the zip could not be assembled."""


# ---------------------------------------------------------------- input types


@dataclass(frozen=True)
class PartLine:
    """One part lot as the instructions name it.

    `colour_rgb` is six hex digits without a leading '#', matching the
    catalogue column and `InventoryLot.colorRgb` in CONTRACT.md. It is optional
    because a handful of catalogue colours carry none, and the document then
    prints the colour name without a swatch rather than printing a wrong one.
    """

    part_num: str
    part_name: str
    colour_name: str
    colour_rgb: str | None
    quantity: int


@dataclass(frozen=True)
class StepSpec:
    index: int
    level: float
    label: str
    parts: tuple[PartLine, ...]

    @property
    def pieces(self) -> int:
        return sum(part.quantity for part in self.parts)

    @property
    def lots(self) -> tuple[PartLine, ...]:
        """The step's parts as a builder counts them, one row per brick.

        `BuildStep.parts` itemises a lot per (part, colour, note), because the
        note is what a client labels a callout with. In a table with no note
        column that reads as the same brick listed twice, which is a real
        defect and not a cosmetic one: the 4-wide car's step 2 printed
        "1 x 4600 Black" on two consecutive lines where the builder needs one
        line saying 2. The step's own heading already carries the notes.
        """
        return tuple(_merge(self.parts))


@dataclass(frozen=True)
class DesignSpec:
    """Everything the package says about a design, in the engine's own words.

    Deliberately not the API's pydantic models: `export` sits at the repository
    root beside the engine it renders, and the service adapts its contract
    shapes into this on the way in. That keeps the dependency pointing one way
    and keeps the document layout free of camelCase field names.
    """

    design_id: str
    title: str
    archetype: str
    variant: str | None
    set_nums: tuple[str, ...]
    # Already translated, and published as `Design.builderNotes`: the document
    # prints what the API served rather than translating a second time, so the
    # page and the printed page cannot disagree about a design's caveats.
    notes: tuple[str, ...]
    steps: tuple[StepSpec, ...]
    ldr_text: str
    ldr_filename: str

    @property
    def pieces(self) -> int:
        return sum(step.pieces for step in self.steps)

    @property
    def slug(self) -> str:
        """Filename stem, in the engine's `<archetype>-<variant>_<sets>` form."""
        stem = f"{self.archetype}-{self.variant}" if self.variant else self.archetype
        return f"{stem}_{'+'.join(self.set_nums)}"


@dataclass(frozen=True)
class RenderedImages:
    """PNG bytes for one design: the finished model, then one image per step."""

    model: bytes
    steps: tuple[bytes, ...]


@dataclass(frozen=True)
class DesignExport:
    """A packaged design, plus the images it was built from.

    The images are kept because the service serves them as endpoints of their
    own (`/designs/{id}/image`, `/designs/{id}/steps/{n}/image`) and they are
    already in hand: rendering them again to answer those would spend the whole
    cost of the package a second time.
    """

    filename: str
    zip_bytes: bytes
    images: RenderedImages

    @property
    def size(self) -> int:
        return len(self.zip_bytes)


# ------------------------------------------------------------------ rendering

# Render sizes in pixels, chosen against where each image is actually used.
# The cover model is placed ~410pt wide and a step image spans the full 499pt
# column, so 1600px puts both near or above 230 DPI - print resolution for the
# pages a builder works from, at about 25-60 kB a PNG.
MODEL_PX = (1600, 1200)
STEP_PX = (1600, 1200)


def render_design(build: Build, renderer: Renderer) -> RenderedImages:
    """Draw the finished model and one image per step.

    The renderer writes files, so a temporary directory is the interface to it;
    the bytes are read back and the directory goes away, because nothing here
    should leave artefacts behind for a later request to find and trust.

    Step images ghost everything already placed - `Renderer.render_steps` does
    that - which is what makes them legible as instructions rather than a
    series of pictures the reader has to diff.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="mocforge-export-") as work:
            directory = Path(work)
            model_path = renderer.render_build(
                build,
                directory / "model.png",
                width=MODEL_PX[0],
                height=MODEL_PX[1],
            )
            step_paths = renderer.render_steps(
                build,
                directory / "steps",
                width=STEP_PX[0],
                height=STEP_PX[1],
            )
            model = model_path.read_bytes()
            steps = tuple(path.read_bytes() for path in step_paths)
    except Exception as exc:
        raise RenderFailed(f"could not render this design: {exc}") from exc
    if not steps:
        raise RenderFailed("the renderer produced no step images")
    return RenderedImages(model=model, steps=steps)


# -------------------------------------------------------------- parts listing


def _merge(parts: Iterable[PartLine]) -> list[PartLine]:
    """Sum lines that name the same brick, most numerous first."""
    totals: dict[tuple[str, str], PartLine] = {}
    for part in parts:
        key = (part.part_num, part.colour_name)
        running = totals.get(key)
        totals[key] = PartLine(
            part_num=part.part_num,
            part_name=part.part_name,
            colour_name=part.colour_name,
            colour_rgb=part.colour_rgb,
            quantity=part.quantity + (running.quantity if running else 0),
        )
    return sorted(
        totals.values(),
        key=lambda part: (-part.quantity, part.part_num, part.colour_name),
    )


def aggregate_parts(spec: DesignSpec) -> list[PartLine]:
    """Every lot the whole design needs, summed across steps.

    The same list backs the CSV and the document's master parts list, so a
    reader ticking off the CSV cannot find it disagreeing with the page they
    are reading it against.
    """
    return _merge(part for step in spec.steps for part in step.parts)


def parts_csv(spec: DesignSpec) -> bytes:
    """The parts list as CSV, for checking pieces off against a real pile.

    Written with CRLF line endings per RFC 4180 and a UTF-8 BOM, because Excel
    on Windows reads a BOM-less UTF-8 CSV as the system codepage and mangles
    every accented colour name in it.
    """
    rows = ["Part number,Part name,Colour,Quantity"]
    rows += [
        ",".join(
            (
                _csv_field(part.part_num),
                _csv_field(part.part_name),
                _csv_field(part.colour_name),
                str(part.quantity),
            )
        )
        for part in aggregate_parts(spec)
    ]
    return ("\r\n".join(rows) + "\r\n").encode("utf-8-sig")


def _csv_field(value: str) -> str:
    """Quote a field if it needs it. Part names contain commas often enough."""
    if any(character in value for character in ',"\r\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


# ------------------------------------------------------------- builder notes

# `Build.warnings` is the engine's own diagnostic text. It names roles by their
# match predicate, measures in LDU and reports deviations from an ideal the
# reader never saw, so printing it verbatim put
#
#   "lowest point is 20.0 LDU vs ground plane 0 (sunk below ground)"
#
# on the cover of a document whose whole job is to give someone the confidence
# to start building. Nothing below is a rewording of everything: a warning is
# either translated into something a builder can act on, or it is dropped.
# A note that cannot change what someone does with a pile of bricks is not a
# note, and an untranslated one is worse than absent. The engineering text is
# not lost - it is still on `Design.warnings` over the API for whoever is
# debugging the generator.

_OPTIONAL_ROLE = re.compile(r"^optional role '(?P<role>.+?)' unfilled")


def _optional_role_note(match: re.Match[str]) -> str:
    part = match.group("role").replace("_", " ").strip().lower()
    return (
        f"This design leaves out an optional {part}: nothing in your inventory "
        "fitted one, so the model is built complete without it."
    )


# Each entry translates one warning the engine can emit. The ground-plane
# warning has no entry on purpose: it measures the model against the internal
# coordinate frame's floor, which a real model cannot be below, and the engine
# itself records it as affecting looks rather than buildability.
_TRANSLATIONS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (_OPTIONAL_ROLE, _optional_role_note),
)


def builder_notes(warnings: Iterable[str]) -> tuple[str, ...]:
    """The subset of the engine's warnings that means something to a builder.

    Called once per design, by the service, which publishes the result as
    `Design.builderNotes` (CONTRACT.md v1.2) and hands it back here inside
    `DesignSpec.notes`. One table, server-side: the web page and this document
    render the same field verbatim, so neither can invent a caveat the other
    does not know about, and the table cannot drift between two languages.

    Order is preserved and duplicates collapse, because two optional roles of
    the same kind are one thing to tell someone once.
    """
    notes: dict[str, None] = {}
    for warning in warnings:
        for pattern, translate in _TRANSLATIONS:
            match = pattern.match(warning)
            if match is not None:
                notes[translate(match)] = None
                break
    return tuple(notes)


def attribution_text(spec: DesignSpec) -> bytes:
    """The plain-text attribution file, which is a legal requirement (R4/R5)."""
    lines = [
        "MOCForge - data sources, licences and trademark notice",
        "",
        f"Design    : {spec.title}",
        f"Built from: {', '.join(spec.set_nums)}",
        "",
        "LEGO catalogue data (set inventories, part names, colours) comes from",
        f"Rebrickable: {REBRICKABLE_URL}",
        "",
        "Part geometry comes from the LDraw Parts Library, licensed CC BY 2.0:",
        f"{LDRAW_URL}",
        f"{CC_BY_URL}",
        "",
        "LEGO® is a trademark of the LEGO Group, which does not sponsor,",
        "authorise or endorse MOCForge or this model.",
        "",
        "This design was generated to fit the inventory above. It is not a LEGO",
        "product, and it is not a redistribution of anyone else's MOC.",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


# --------------------------------------------------------------- pdf document

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 48.0
FOOTER_BASELINE = 34.0
CONTENT_TOP = PAGE_HEIGHT - 54.0
CONTENT_BOTTOM = 62.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

# The web client's design tokens, so a printed page and the screen it came from
# are recognisably the same product (web/src/styles/tokens.css).
INK = (0.102, 0.094, 0.082)
INK_MUTED = (0.361, 0.341, 0.310)
INK_SUBTLE = (0.431, 0.412, 0.380)
ACCENT = (0.122, 0.306, 0.372)
SUNKEN = (0.953, 0.945, 0.925)
BORDER = (0.831, 0.812, 0.773)
BORDER_SUBTLE = (0.886, 0.871, 0.839)

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

# Table columns, as (heading, width in points); a heading of None is the
# tick-box column. Each set sums to CONTENT_WIDTH, 499.3pt.
PARTS_COLUMNS: tuple[tuple[str | None, float], ...] = (
    (None, 18.0), ("Qty", 32.0), ("Part number", 78.0),
    ("Part name", 225.3), ("Colour", 146.0),
)
STEP_COLUMNS: tuple[tuple[str | None, float], ...] = (
    ("Qty", 32.0), ("Part number", 78.0), ("Part name", 243.3), ("Colour", 146.0),
)

ROW_HEIGHT = 15.0
ROW_TEXT_SIZE = 8.6
# Where a row's text sits below the cursor, so the baseline, the stripe, the
# tick box and the colour chip all line up from one number.
ROW_BASELINE = 10.4


class _Sheet:
    """A paginated canvas with a cursor and a fixed footer.

    Written against the raw canvas rather than platypus because every page here
    is a full-width image with a table under it - flowables would be fighting
    for control of a layout that has none of the reflowing prose they exist to
    handle. What the canvas lacks is any sense of running out of room, and that
    is what this adds: `ensure` breaks the page, and `y` is always the next
    cursor position.
    """

    def __init__(self, buffer: io.BytesIO, spec: DesignSpec) -> None:
        self.canvas = canvas.Canvas(buffer, pagesize=A4, invariant=1)
        self.canvas.setTitle(f"{spec.title} - MOCForge build instructions")
        self.canvas.setAuthor("MOCForge (generated)")
        self.canvas.setSubject(ATTRIBUTION_FOOTER)
        self._running_head = f"{spec.title} - built from {', '.join(spec.set_nums)}"
        self._page = 1
        self.y = CONTENT_TOP

    # ------------------------------------------------------------ page frame

    def _footer(self) -> None:
        pdf = self.canvas
        pdf.setStrokeColorRGB(*BORDER_SUBTLE)
        pdf.setLineWidth(0.5)
        pdf.line(MARGIN, FOOTER_BASELINE + 14, PAGE_WIDTH - MARGIN, FOOTER_BASELINE + 14)
        pdf.setFont(BODY_FONT, 6.8)
        pdf.setFillColorRGB(*INK_SUBTLE)
        pdf.drawString(MARGIN, FOOTER_BASELINE + 4, ATTRIBUTION_FOOTER)
        pdf.drawString(
            MARGIN, FOOTER_BASELINE - 5,
            self.clip(self._running_head, 6.8, CONTENT_WIDTH - 60),
        )
        pdf.drawRightString(
            PAGE_WIDTH - MARGIN, FOOTER_BASELINE - 5, f"Page {self._page}"
        )

    def break_page(self) -> None:
        self._footer()
        self.canvas.showPage()
        self._page += 1
        self.y = CONTENT_TOP

    def ensure(self, height: float) -> bool:
        """Start a new page unless `height` still fits. True if it broke."""
        if self.y - height >= CONTENT_BOTTOM:
            return False
        self.break_page()
        return True

    def finish(self) -> None:
        self._footer()
        self.canvas.save()

    # ----------------------------------------------------------------- marks

    def text(
        self,
        value: str,
        size: float,
        *,
        bold: bool = False,
        colour: tuple[float, float, float] = INK,
        leading: float | None = None,
        tracking: float = 0.0,
    ) -> None:
        pdf = self.canvas
        font = BOLD_FONT if bold else BODY_FONT
        pdf.setFont(font, size)
        pdf.setFillColorRGB(*colour)
        if tracking:
            _tracked(pdf, MARGIN, self.y - size, value, font, size, tracking)
        else:
            pdf.drawString(MARGIN, self.y - size, value)
        self.y -= leading if leading is not None else size * 1.35

    def paragraph(
        self,
        value: str,
        size: float,
        *,
        colour: tuple[float, float, float] = INK_MUTED,
        width: float = CONTENT_WIDTH,
    ) -> None:
        for line in self._wrap(value, size, width):
            self.ensure(size * 1.5)
            self.text(line, size, colour=colour, leading=size * 1.5)

    def rule(self, colour: tuple[float, float, float] = BORDER) -> None:
        self.canvas.setStrokeColorRGB(*colour)
        self.canvas.setLineWidth(0.6)
        self.canvas.line(MARGIN, self.y, PAGE_WIDTH - MARGIN, self.y)
        self.y -= 1

    def space(self, height: float) -> None:
        self.y -= height

    def image(self, png: bytes, max_height: float) -> None:
        """Place a PNG centred, scaled to fit the column and `max_height`."""
        reader = ImageReader(io.BytesIO(png))
        pixel_width, pixel_height = reader.getSize()
        scale = min(CONTENT_WIDTH / pixel_width, max_height / pixel_height)
        width, height = pixel_width * scale, pixel_height * scale
        self.ensure(height + 8)
        x = MARGIN + (CONTENT_WIDTH - width) / 2
        self.canvas.drawImage(reader, x, self.y - height, width=width, height=height)
        self.y -= height + 8

    def swatch(self, x: float, rgb: str | None) -> float:
        """A colour chip, returning the x its label should start at.

        "Light Bluish Gray" means nothing to someone holding a pile of bricks;
        the chip is what makes the colour column usable at a glance.
        """
        parsed = _parse_rgb(rgb)
        if parsed is None:
            return x
        size = 7.5
        self.canvas.setFillColorRGB(*parsed)
        self.canvas.setStrokeColorRGB(*BORDER)
        self.canvas.setLineWidth(0.4)
        self.canvas.rect(x, self.y - ROW_BASELINE + 0.8, size, size, fill=1, stroke=1)
        return x + size + 4

    def checkbox(self, x: float) -> None:
        self.canvas.setFillColorRGB(1, 1, 1)
        self.canvas.setStrokeColorRGB(*BORDER)
        self.canvas.setLineWidth(0.6)
        self.canvas.rect(x, self.y - ROW_BASELINE + 0.2, 8.0, 8.0, fill=1, stroke=1)

    def stripe(self) -> None:
        """A faint band behind alternate rows, so a wide row reads across."""
        self.canvas.setFillColorRGB(*SUNKEN)
        self.canvas.rect(
            MARGIN - 4, self.y - ROW_HEIGHT + 3.5, CONTENT_WIDTH + 8, ROW_HEIGHT,
            fill=1, stroke=0,
        )

    # ----------------------------------------------------------- measurement

    def width_of(self, value: str, size: float, *, bold: bool = False) -> float:
        return self.canvas.stringWidth(value, BOLD_FONT if bold else BODY_FONT, size)

    def clip(self, value: str, size: float, width: float) -> str:
        """Trim to fit, with an ASCII ellipsis rather than U+2026.

        The base-14 fonts are used unembedded, and a glyph the encoding does not
        carry is dropped silently rather than reported - so nothing outside
        plain ASCII goes into a measured, truncated string.
        """
        if self.width_of(value, size) <= width:
            return value
        clipped = value
        while clipped and self.width_of(clipped + "...", size) > width:
            clipped = clipped[:-1]
        return clipped.rstrip() + "..."

    def _wrap(self, value: str, size: float, width: float) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in value.split():
            trial = f"{current} {word}".strip()
            if current and self.width_of(trial, size) > width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        return lines


def _tracked(
    pdf: canvas.Canvas,
    x: float,
    y: float,
    value: str,
    font: str,
    size: float,
    tracking: float,
) -> None:
    """Draw letter-spaced text, which the small-caps labels are set in.

    Character spacing lives on a PDF text object rather than on the canvas, and
    it has to be put back: `Tc` is part of the page's text state, which `ET`
    does *not* reset, so spacing set inside one text object leaks into every
    string drawn after it on that page. That is not theoretical - it shipped in
    the first draft of this document and set the cover's body copy 1.4pt loose,
    which also pushed it past the measured right margin, because the wrapper
    measures untracked text.
    """
    text = pdf.beginText(x, y)
    text.setFont(font, size)
    text.setCharSpace(tracking)
    text.textOut(value)
    text.setCharSpace(0)
    pdf.drawText(text)


def _tracked_width(pdf: canvas.Canvas, value: str, font: str, size: float,
                   tracking: float) -> float:
    return pdf.stringWidth(value, font, size) + tracking * len(value)


def _parse_rgb(rgb: str | None) -> tuple[float, float, float] | None:
    """Six hex digits to a 0..1 triple, or None if the catalogue has no colour."""
    if rgb is None or len(rgb) != 6:
        return None
    try:
        red, green, blue = (int(rgb[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None
    return (red / 255, green / 255, blue / 255)


def _column_positions(columns: Sequence[tuple[str | None, float]]) -> list[float]:
    positions: list[float] = []
    x = MARGIN
    for _, width in columns:
        positions.append(x)
        x += width
    return positions


HEAD_SIZE = 7.4
HEAD_TRACKING = 0.5


def _table_head(sheet: _Sheet, columns: Sequence[tuple[str | None, float]]) -> None:
    pdf = sheet.canvas
    pdf.setFont(BOLD_FONT, HEAD_SIZE)
    pdf.setFillColorRGB(*INK_SUBTLE)
    baseline = sheet.y - 8
    for (heading, width), x in zip(columns, _column_positions(columns), strict=True):
        if heading is None:  # the tick-box column, which needs no heading
            continue
        label = heading.upper()
        left = x
        if heading == "Qty":  # right-aligned over the digits it labels
            left = x + width - 6 - _tracked_width(
                pdf, label, BOLD_FONT, HEAD_SIZE, HEAD_TRACKING
            )
        _tracked(pdf, left, baseline, label, BOLD_FONT, HEAD_SIZE, HEAD_TRACKING)
    sheet.y -= 11
    sheet.rule(BORDER)
    sheet.y -= 4


def _part_row(
    sheet: _Sheet,
    part: PartLine,
    columns: Sequence[tuple[str | None, float]],
    *,
    striped: bool,
) -> None:
    if striped:
        sheet.stripe()
    positions = _column_positions(columns)
    pdf = sheet.canvas
    first = 0
    if columns[0][0] is None:
        sheet.checkbox(positions[0] + 2)
        first = 1

    baseline = sheet.y - ROW_BASELINE
    quantity_x, quantity_width = positions[first], columns[first][1]
    pdf.setFont(BOLD_FONT, ROW_TEXT_SIZE)
    pdf.setFillColorRGB(*INK)
    pdf.drawRightString(quantity_x + quantity_width - 6, baseline, str(part.quantity))

    pdf.setFont(BODY_FONT, ROW_TEXT_SIZE)
    pdf.setFillColorRGB(*INK_MUTED)
    pdf.drawString(positions[first + 1], baseline, part.part_num)

    pdf.setFillColorRGB(*INK)
    name_width = columns[first + 2][1] - 8
    pdf.drawString(
        positions[first + 2], baseline, sheet.clip(part.part_name, ROW_TEXT_SIZE, name_width)
    )

    colour_x = sheet.swatch(positions[first + 3], part.colour_rgb)
    pdf.setFont(BODY_FONT, ROW_TEXT_SIZE)
    pdf.setFillColorRGB(*INK_MUTED)
    colour_width = columns[first + 3][1] - (colour_x - positions[first + 3]) - 2
    pdf.drawString(
        colour_x, baseline, sheet.clip(part.colour_name, ROW_TEXT_SIZE, colour_width)
    )
    sheet.y -= ROW_HEIGHT


def _part_table(
    sheet: _Sheet,
    parts: Iterable[PartLine],
    columns: Sequence[tuple[str | None, float]],
    *,
    continued: str,
) -> None:
    """Draw a part table, repeating its heading on every page it spills onto.

    A single step can be large - a one-layer mosaic is 40 lots in one step - so
    spilling is the normal case for some designs, not a guard against one.
    """
    _table_head(sheet, columns)
    for number, part in enumerate(parts):
        if sheet.ensure(ROW_HEIGHT + 4):
            sheet.text(continued, 11, bold=True)
            sheet.space(6)
            _table_head(sheet, columns)
        _part_row(sheet, part, columns, striped=number % 2 == 1)


STAT_BAND = 44.0
STAT_SIZES = (15.0, 12.5, 10.5)


def _stat_lines(sheet: _Sheet, value: str, width: float) -> tuple[list[str], float]:
    """Type size and line breaks for one stat value, chosen so it always fits.

    Three of the four cells hold a small number and never trouble this. The
    fourth holds the design's kind, and `technic_crib/tower` at the display
    size overflowed its cell: the value rendered as `technic_crib/...`, an
    ellipsis standing exactly where the one thing the cell exists to say
    should be. Clipping is never the answer for a value - so the size steps
    down first, because one line reads best, then the name breaks at its
    separator onto a second, and finally the size is scaled to the widest
    line. The result fits by construction rather than by the archetype names
    happening to be short.

    The last step trades legibility for completeness without a floor, which is
    the right way round here but is worth knowing: an absurd 40-character
    archetype name would be set very small rather than cropped or allowed to
    run into the next cell. Every name in the template library today fits on
    one line at 12.5pt or better.
    """
    for size in STAT_SIZES:
        if sheet.width_of(value, size, bold=True) <= width:
            return [value], size
    smallest = STAT_SIZES[-1]
    head, separator, tail = value.partition("/")
    lines = [head, tail] if separator and tail else [value]
    widest = max(sheet.width_of(line, smallest, bold=True) for line in lines)
    return lines, min(smallest, smallest * width / widest) if widest else smallest


def _stat_row(sheet: _Sheet, entries: Sequence[tuple[str, str]]) -> None:
    """A row of value-over-label cells, as the web app shows them."""
    pdf = sheet.canvas
    cell = CONTENT_WIDTH / len(entries)
    top = sheet.y
    pdf.setFillColorRGB(*SUNKEN)
    pdf.rect(MARGIN, top - STAT_BAND, CONTENT_WIDTH, STAT_BAND, fill=1, stroke=0)
    for index, (value, label) in enumerate(entries):
        x = MARGIN + index * cell + 12
        lines, size = _stat_lines(sheet, value, cell - 24)
        pdf.setFont(BOLD_FONT, size)
        pdf.setFillColorRGB(*INK)
        # The last line takes the row's single baseline, so every label stays
        # aligned however many lines the value above it needed, and earlier
        # lines stack above it. Drawn in reading order, which is both the order
        # they appear on the page and the order they come out of the file.
        leading = size * 1.12
        baseline = top - 25 + leading * (len(lines) - 1)
        for line in lines:
            pdf.drawString(x, baseline, line)
            baseline -= leading
        pdf.setFont(BODY_FONT, HEAD_SIZE)
        pdf.setFillColorRGB(*INK_SUBTLE)
        _tracked(pdf, x, top - 37, label.upper(), BODY_FONT, HEAD_SIZE, HEAD_TRACKING)
        if index:
            pdf.setStrokeColorRGB(*BORDER)
            pdf.setLineWidth(0.5)
            pdf.line(MARGIN + index * cell, top - 37, MARGIN + index * cell, top - 9)
    sheet.y = top - STAT_BAND


def _kind(spec: DesignSpec) -> str:
    return f"{spec.archetype}/{spec.variant}" if spec.variant else spec.archetype


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else noun + "s"


def _cover(sheet: _Sheet, spec: DesignSpec, images: RenderedImages) -> None:
    sheet.text(
        "MOCFORGE BUILD INSTRUCTIONS", 8, bold=True, colour=ACCENT, tracking=1.4,
        leading=16,
    )
    sheet.text(sheet.clip(spec.title, 26, CONTENT_WIDTH), 26, bold=True, leading=30)
    sheet.text(
        f"Built from {', '.join(spec.set_nums)} - and from nothing else.",
        10, colour=INK_MUTED, leading=18,
    )
    sheet.image(images.model, 310)
    _stat_row(
        sheet,
        (
            (str(spec.pieces), "pieces"),
            (str(len(spec.steps)), "steps"),
            (str(len(aggregate_parts(spec))), "part lots"),
            (_kind(spec), "design"),
        ),
    )
    sheet.space(16)
    sheet.paragraph(
        "Every part in this model is one you already own: the generator was "
        "given the inventory of the sets above and allowed nothing outside it. "
        "Build the steps in order. Each adds a course of parts on top of the "
        "last, and its picture shows the pieces you add in colour against "
        "everything already built in grey.",
        9.2,
    )
    if spec.notes:
        sheet.space(10)
        sheet.text("Before you start", 9.6, bold=True, leading=13)
        for note in spec.notes:
            sheet.paragraph(f"- {note}", 8.8)
    sheet.space(12)
    sheet.text("In this pack", 9.6, bold=True, leading=13)
    for line in (
        "instructions.pdf - this document",
        "parts-list.csv - the parts list as a spreadsheet, to check pieces off",
        f"steps/ - every step image as a PNG, numbered 01 to {len(spec.steps):02d}",
        "finished-model.png - the picture on this page",
        f"{spec.ldr_filename} - the LDraw model, for Stud.io, LeoCAD or LDView",
        "ATTRIBUTION.txt - data sources, licences and the trademark notice",
    ):
        sheet.paragraph(f"- {line}", 8.8)
    # Anchored to the foot of the cover rather than left floating under the
    # last list item, so the notice reads as the page's colophon.
    sheet.y = CONTENT_BOTTOM + 10 + 10 * len(ATTRIBUTION_LINES)
    sheet.rule(BORDER_SUBTLE)
    sheet.space(8)
    for line in ATTRIBUTION_LINES:
        sheet.text(line, 7.6, colour=INK_SUBTLE, leading=10)


def _parts_page(sheet: _Sheet, spec: DesignSpec) -> None:
    sheet.break_page()
    sheet.text("Parts you will need", 18, bold=True, leading=23)
    parts = aggregate_parts(spec)
    sheet.paragraph(
        f"{spec.pieces} pieces in {len(parts)} {_plural(len(parts), 'lot')}. "
        "Colour names are "
        "Rebrickable's and each chip is that colour's own RGB, so a lot can be "
        "matched by eye as well as by name. The same list is in parts-list.csv.",
        9,
    )
    sheet.space(10)
    _part_table(
        sheet, parts, PARTS_COLUMNS, continued="Parts you will need (continued)"
    )
    sheet.space(5)
    sheet.rule(BORDER)
    sheet.space(4)
    sheet.text(f"{spec.pieces} pieces in total", 9, bold=True, leading=12)


def _step_page(sheet: _Sheet, spec: DesignSpec, step: StepSpec, image: bytes) -> None:
    sheet.break_page()
    sheet.text(
        f"STEP {step.index} OF {len(spec.steps)}", 8, bold=True, colour=ACCENT,
        tracking=1.4, leading=14,
    )
    sheet.text(sheet.clip(step.label, 17, CONTENT_WIDTH), 17, bold=True, leading=20)
    level = (
        "on the ground" if step.level == 0
        else f"{step.level:g} plates above the ground"
    )
    lots = step.lots
    sheet.text(
        f"{step.pieces} {_plural(step.pieces, 'piece')} in "
        f"{len(lots)} {_plural(len(lots), 'lot')}, {level}.",
        9, colour=INK_MUTED, leading=14,
    )
    # A step is one page, so the picture gets most of it: this is the thing the
    # reader works from, and the parts table under it is the caption.
    sheet.image(image, 380)
    if step.index == 1:
        sheet.text(
            "In every picture, grey pieces are ones you have already placed "
            "and the pieces in colour are the ones to add.",
            8.4, colour=INK_SUBTLE, leading=15,
        )
    sheet.space(4)
    _part_table(
        sheet, lots, STEP_COLUMNS,
        continued=f"Step {step.index} of {len(spec.steps)} (continued)",
    )


def instructions_pdf(spec: DesignSpec, images: RenderedImages) -> bytes:
    """The printable document: cover, parts list, then one page per step."""
    buffer = io.BytesIO()
    sheet = _Sheet(buffer, spec)
    _cover(sheet, spec, images)
    _parts_page(sheet, spec)
    for step, image in zip(spec.steps, images.steps, strict=True):
        _step_page(sheet, spec, step, image)
    sheet.finish()
    return buffer.getvalue()


# ------------------------------------------------------------------ packaging

# A fixed member date, so the same design packages to the same bytes. 1980-01-01
# is the earliest a zip can express, and the convention for reproducible builds.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def package(spec: DesignSpec, images: RenderedImages) -> DesignExport:
    """Assemble the zip. Pure: no clock, no filesystem, no network.

    Raises `PackageFailed` for anything that stops the document or the archive
    being written, which the service reports separately from a render failure -
    the two have nothing in common but their status code.
    """
    if len(images.steps) != len(spec.steps):
        raise PackageFailed(
            f"{len(images.steps)} step images for {len(spec.steps)} steps: the "
            "renderer and the engine disagree about how this design is stepped"
        )
    try:
        document = instructions_pdf(spec, images)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            _write(archive, "instructions.pdf", document)
            _write(archive, "parts-list.csv", parts_csv(spec))
            _write(archive, spec.ldr_filename, spec.ldr_text.encode("utf-8"))
            _write(archive, "ATTRIBUTION.txt", attribution_text(spec))
            # A PNG is already deflated; compressing it again costs CPU for
            # nothing measurable, so the images are stored.
            _write(archive, "finished-model.png", images.model, compress=False)
            for index, image in enumerate(images.steps, start=1):
                _write(archive, f"steps/step-{index:02d}.png", image, compress=False)
    except PackageFailed:
        raise
    except Exception as exc:
        raise PackageFailed(f"could not write the package: {exc}") from exc
    return DesignExport(
        filename=f"mocforge_{spec.slug}.zip",
        zip_bytes=buffer.getvalue(),
        images=images,
    )


def _write(
    archive: zipfile.ZipFile, name: str, data: bytes, *, compress: bool = True
) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build_export(spec: DesignSpec, build: Build, renderer: Renderer) -> DesignExport:
    """Render a design and package it, which is what the service calls."""
    return package(spec, render_design(build, renderer))
