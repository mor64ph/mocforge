"""The generation engine, wrapped for HTTP use.

Everything expensive - the read-only SQLite catalogue, the LDraw part index and
the fitter - is built once and reused. Nothing here reaches the network: the
only inputs are `data/mocforge.db` and `data/ldraw/complete.zip`, which is PRD
R1 (no request ever goes to rebrickable.com, and `rebrickable_api.py` is not
imported anywhere in this package).

Threading. All engine state is confined to one worker thread, and `EngineHandle`
is the only way in. `Catalogue` now accepts `check_same_thread=False`, so the
catalogue connection alone could be shared, but that does not make the rest
shareable and it is not the binding constraint:

  * `LDrawLibrary` reads parts out of a single open `zipfile.ZipFile`.
    Concurrent reads on one zip handle interleave seeks on the shared file
    object and return corrupt data; it also memoises parsed geometry in a plain
    dict. Every generation and every inventory profile goes through it.
  * `Fitter` memoises colour matches and `Taxonomy` memoises part roles, both
    in plain dicts.

The alternative - a connection and a library per thread - would duplicate the
geometry cache and the part-name tables per thread, which is exactly the memory
that makes repeat requests fast. So requests are serialised instead,
deliberately: generation is seconds long, the caches make repeats cheap, and
`/health` and `/archetypes` are served from startup snapshots so a liveness
probe never queues behind a build. Making the catalogue-only endpoints
(`/sets/search`, `/sets/{setNum}`) concurrent is now possible and is the first
thing to do if search latency under load ever matters; it is left alone while
one warm cache is worth more than parallel index lookups.

Handlers stay `async def` and await the worker, so serialised engine work never
blocks the event loop and one slow build cannot stall the rest of the server.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import sqlite3
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from . import REPO_ROOT  # noqa: F401  (puts the engine modules on sys.path)
from .errors import (
    DESIGN_NOT_FOUND,
    INVALID_REQUEST,
    PACKAGE_FAILED,
    RENDER_FAILED,
    SERVICE_UNAVAILABLE,
    SET_NOT_FOUND,
    STEP_NOT_FOUND,
    ApiError,
)
from .models import (
    ArchetypeInfo,
    ArchetypesResponse,
    BuildStep,
    CatalogueInfo,
    Design,
    FamilyCount,
    GenerateResponse,
    HealthResponse,
    Inventory,
    SetSummary,
    StepPart,
    WithheldDesign,
)

from export import (
    DesignExport,
    DesignSpec,
    PackageFailed,
    PartLine,
    RenderFailed,
    StepSpec,
    build_export,
    builder_notes,
)
from generate import MIN_PIECES, TEMPLATES, Build, Fitter, PlacedPart, Template
from inventory import Catalogue
from ldraw import LDrawLibrary
from render import Renderer
from roles import Profile, Taxonomy

logger = logging.getLogger("mocforge.api")

T = TypeVar("T")

# The contract states the API only ever returns designs at confidence 1.0 and
# that a client may rely on it. `Build.buildable` now enforces that itself -
# `weak` placements block a build, so a buildable build cannot score below 1.0 -
# which makes this floor redundant rather than wrong. It is kept as a guard: it
# is the one line that would catch a future scoring change quietly reintroducing
# sub-1.0 designs, and it costs one comparison per build.
CONFIDENCE_FLOOR = 1.0

# A set number is a few characters of alphanumerics plus separators. Anything
# else is a malformed request (400), not an unknown set (404) - the distinction
# matters to a client deciding between "no such set" and "that is not a set
# number".
SET_NUM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

# Cache sizes. The design registry has to outlive the generate cache, or a
# cached response could hand out an `ldrUrl` whose design has been evicted:
# 64 inventories x at most a dozen designs each fits comfortably in 4,096.
GENERATE_CACHE_SIZE = 64
INVENTORY_CACHE_SIZE = 256
DESIGN_REGISTRY_SIZE = 4096

# Packages are the one cache measured in megabytes rather than entries.
# Measured: a design packages to 130-640 kB of zip, and the entry holds the
# same images again in raw form, so about 1 MB each. Sixteen is a few tens of
# megabytes against a 240 MB catalogue, and covers a session that downloads
# every design of two or three inventories.
EXPORT_CACHE_SIZE = 16

# v1.1 features, named so a client can discover them from `/health` instead of
# probing an endpoint that might 404 for a different reason. CONTRACT.md makes
# this the sanctioned discovery mechanism, and the web client will not offer a
# download unless the service it is talking to lists `designPackage`.
FEATURES = ("designImage", "stepImage", "designPackage")

# SQLite LIKE escape character, kept out of the SQL string so the backslash
# does not have to survive two layers of quoting.
LIKE_ESCAPE = "\\"


@dataclass(frozen=True)
class DesignRecord:
    """A returned design, the LDraw text `GET .../ldr` serves, and its build.

    The `Build` is kept because the v1.1 image and package endpoints have to
    render it. Re-deriving it from (archetype, variant, setNums) would cost
    another run of the fitter - seconds of geometry work for something already
    computed - and would give the pictures a second chance to disagree with the
    steps the client was already shown. A build holds one `PlacedPart` per
    piece and shares its geometry with the library's cache, so a 236-part
    design is tens of kilobytes, not megabytes.
    """

    design: Design
    ldr: str
    filename: str
    build: Build
    set_nums: tuple[str, ...]


def export_spec(
    record: DesignRecord, colour_rgb: Mapping[int, str | None]
) -> DesignSpec:
    """Adapt a record into what `export.py` packages.

    A pure rename of `Design.steps` and `Design.builderNotes`, not a second
    derivation of either: the document must itemise exactly the steps the
    client was shown and repeat exactly the notes it published, and anything
    that re-grouped or re-translated here could drift from what the client
    holds without either side being wrong on its own terms.
    """
    steps = tuple(
        StepSpec(
            index=step.index,
            level=step.level,
            label=_step_label(step),
            parts=tuple(
                PartLine(
                    part_num=part.partNum,
                    part_name=part.partName,
                    colour_name=part.colorName,
                    colour_rgb=colour_rgb.get(part.colorId),
                    quantity=part.quantity,
                )
                for part in step.parts
            ),
        )
        for step in record.design.steps
    )
    return DesignSpec(
        design_id=record.design.id,
        title=record.design.title,
        archetype=record.design.archetype,
        variant=record.design.variant,
        set_nums=record.set_nums,
        notes=tuple(record.design.builderNotes),
        steps=steps,
        ldr_text=record.ldr,
        ldr_filename=record.filename,
    )


def _step_label(step: BuildStep) -> str:
    """A heading for a step, from the notes the engine put on its parts.

    The engine labels each placement with what it is for ("wall course 2",
    "roof"), which is more use as a step title than the level number the step
    is keyed on. Notes are de-duplicated in order of appearance, so a step that
    lays a course and its floor reads as both.
    """
    notes = list(dict.fromkeys(part.note for part in step.parts if part.note))
    if not notes:
        return f"Place these {sum(part.quantity for part in step.parts)} parts"
    label = "; ".join(notes)
    # Only the first character is raised: `str.capitalize` would lower-case the
    # rest, and a note can carry a part name or a number that owns its case.
    return label[0].upper() + label[1:]


def design_identity(archetype: str, variant: str | None, set_nums: Sequence[str]) -> str:
    """Stable opaque id for an (archetype, variant, setNums) triple.

    A hash rather than a counter, so the same request yields the same id in
    every process and after a restart, and opaque per contract rule 5 - the
    client must not be able to read an archetype out of it. Set numbers are
    sorted because owning A and B is the same inventory as owning B and A.
    """
    seed = "\x1f".join([archetype, variant or "", *sorted(set_nums)])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _ms_since(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _like_escape(value: str) -> str:
    """Escape LIKE wildcards, so a search for `10%` does not match everything."""
    out = value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
    for wildcard in ("%", "_"):
        out = out.replace(wildcard, LIKE_ESCAPE + wildcard)
    return out


class Engine:
    """Synchronous engine facade. Every method must run on the owner thread."""

    def __init__(
        self,
        cat: Catalogue,
        fitter: Fitter,
        taxonomy: Taxonomy,
        renderer: Renderer,
    ) -> None:
        # The LDraw library is not held here: `Fitter` and `Taxonomy` share the
        # one instance, and a second reference would invite a second cache.
        self._cat = cat
        self._fitter = fitter
        self._taxonomy = taxonomy
        self._renderer = renderer

        self._part_names: dict[str, str] = {
            row["part_num"]: row["name"]
            for row in cat.conn.execute("SELECT part_num, name FROM parts")
        }
        self._colour_names: dict[int, str] = {
            row["id"]: row["name"]
            for row in cat.conn.execute("SELECT id, name FROM colors")
        }
        # Read for the document's colour chips. A name like "Light Bluish Gray"
        # tells someone sorting a pile of bricks nothing; the colour does.
        self._colour_rgb: dict[int, str | None] = {
            row["id"]: row["rgb"]
            for row in cat.conn.execute("SELECT id, rgb FROM colors")
        }

        self._health = self._read_health()
        self._archetypes = self._read_archetypes()

        self._inventories: OrderedDict[tuple[str, ...], Inventory] = OrderedDict()
        self._generated: OrderedDict[
            tuple[tuple[str, ...], tuple[str, ...]],
            tuple[GenerateResponse, tuple[DesignRecord, ...]],
        ] = OrderedDict()
        self._designs: OrderedDict[str, DesignRecord] = OrderedDict()
        self._exports: OrderedDict[str, DesignExport] = OrderedDict()

    # ------------------------------------------------------------- lifecycle

    @classmethod
    def load(cls) -> "Engine":
        """Build the whole engine. Runs on the worker thread, never per request."""
        started = time.perf_counter()
        cat = Catalogue()
        lib = LDrawLibrary()
        fitter = Fitter(cat, lib)
        taxonomy = Taxonomy(cat, lib)
        # The renderer parses the LDraw colour definitions on construction,
        # which is another read of the 145 MB zip, so it is built here with
        # everything else expensive rather than per download.
        engine = cls(cat, fitter, taxonomy, Renderer())
        catalogue = engine.health().catalogue
        logger.info(
            "engine ready in %dms: %d sets, %d parts, %d archetypes",
            _ms_since(started),
            catalogue.sets,
            catalogue.parts,
            len(engine.archetypes().archetypes),
        )
        return engine

    def close(self) -> None:
        try:
            self._cat.conn.close()
        except sqlite3.Error:  # pragma: no cover - closing a read-only handle
            logger.warning("catalogue connection did not close cleanly")

    def _read_health(self) -> HealthResponse:
        sets = self._cat.conn.execute("SELECT COUNT(*) AS n FROM sets").fetchone()["n"]
        parts = self._cat.conn.execute("SELECT COUNT(*) AS n FROM parts").fetchone()["n"]
        row = self._cat.conn.execute(
            "SELECT MAX(ts) AS ts FROM ingest_log WHERE action IN ('build', 'download')"
        ).fetchone()
        updated: str | None = row["ts"] if row is not None else None
        if not updated:
            # Falls back to the database file's own timestamp, so `updatedAt` is
            # never a lie about how fresh the catalogue is.
            database = Path(self._cat.conn.execute("PRAGMA database_list").fetchone()[2])
            updated = dt.datetime.fromtimestamp(
                database.stat().st_mtime, tz=dt.timezone.utc
            ).isoformat()
        return HealthResponse(
            status="ok",
            catalogue=CatalogueInfo(sets=sets, parts=parts, updatedAt=updated),
            features=list(FEATURES),
        )

    def _read_archetypes(self) -> ArchetypesResponse:
        """Snapshot the template library.

        Read from `generate.TEMPLATES` rather than from a list of names held
        here, so an archetype added by the engine workstream shows up without
        this service being edited. A template whose only variant carries no
        name reports no variants, which is the honest answer: it has exactly
        one unnamed configuration.
        """
        archetypes = [
            ArchetypeInfo(
                name=name,
                title=tpl.title,
                variants=[v["name"] for v in tpl.variants if v.get("name")],
                studded=tpl.studded,
            )
            for name, tpl in TEMPLATES.items()
        ]
        return ArchetypesResponse(archetypes=archetypes)

    # ------------------------------------------------------------- endpoints

    def health(self) -> HealthResponse:
        return self._health

    def archetypes(self) -> ArchetypesResponse:
        return self._archetypes

    def search(self, q: str, limit: int) -> list[SetSummary]:
        """Search by set number or name, ranked as the contract specifies.

        Tier 0 is an exact set-number match, including a bare number typed
        without its `-1` suffix - `Catalogue.resolve_set` does that resolution,
        and it is the same code path the rest of the service uses, so search
        can never disagree with what `POST /inventory` will accept. Tier 1 is a
        set-number prefix, tier 2 a name match, ordered by piece count
        descending.
        """
        term = q.strip()
        if not term:  # unreachable through the query model; kept for direct calls
            raise ApiError(400, INVALID_REQUEST, "q must not be empty")
        canonical = self._cat.resolve_set(term) if SET_NUM_RE.match(term) else None
        pattern = _like_escape(term)
        sql = """
            SELECT s.set_num, s.name, s.year, s.theme_id, t.name AS theme_name,
                   s.num_parts, s.img_url,
                   CASE
                     WHEN s.set_num = :canonical COLLATE NOCASE THEN 0
                     WHEN s.set_num LIKE :prefix ESCAPE :esc THEN 1
                     ELSE 2
                   END AS tier
            FROM sets s
            LEFT JOIN themes t ON t.id = s.theme_id
            WHERE s.set_num = :canonical COLLATE NOCASE
               OR s.set_num LIKE :prefix ESCAPE :esc
               OR s.name LIKE :contains ESCAPE :esc
            ORDER BY tier ASC, s.num_parts DESC, s.set_num ASC
            LIMIT :limit
        """
        rows = self._cat.conn.execute(
            sql,
            {
                "canonical": canonical or term,
                "prefix": pattern + "%",
                "contains": "%" + pattern + "%",
                "esc": LIKE_ESCAPE,
                "limit": limit,
            },
        ).fetchall()
        return [self._summary_from_row(row) for row in rows]

    def set_summary(self, raw: str) -> SetSummary:
        set_num = self._resolve_one(raw)
        row = self._cat.conn.execute(
            """SELECT s.set_num, s.name, s.year, s.theme_id, t.name AS theme_name,
                      s.num_parts, s.img_url
               FROM sets s LEFT JOIN themes t ON t.id = s.theme_id
               WHERE s.set_num = ?""",
            (set_num,),
        ).fetchone()
        if row is None:  # pragma: no cover - resolve_set only returns real sets
            raise ApiError(404, SET_NOT_FOUND, "no set " + repr(raw) + " in the catalogue")
        return self._summary_from_row(row)

    def inventory(self, raw: Sequence[str]) -> Inventory:
        return self._inventory_for(self._resolve_all(raw))

    def generate(
        self, raw: Sequence[str], archetypes: Sequence[str] | None
    ) -> GenerateResponse:
        """Run the selected archetypes over an inventory and offer what validates.

        Cached on `(sorted set numbers, sorted archetype names)`: generation is
        seconds of geometry work and its result is a pure function of those two
        things. An absent filter is normalised to the full archetype list, so
        `archetypes: null` and an explicit list of everything share one entry
        instead of computing the same answer twice. `elapsedMs` is always the
        real time this request took, so a cache hit reports itself as one.
        """
        started = time.perf_counter()
        resolved = self._resolve_all(raw)
        names = self._select_archetypes(archetypes)
        key = (resolved, tuple(sorted(names)))

        cached = self._generated.get(key)
        if cached is not None:
            self._generated.move_to_end(key)
            response, records = cached
            for record in records:
                self._register(record)  # keeps every cached ldrUrl resolvable
            return response.model_copy(update={"elapsedMs": _ms_since(started)})

        inventory = self._inventory_for(resolved)
        designs: list[Design] = []
        withheld: list[WithheldDesign] = []
        records: list[DesignRecord] = []

        if inventory.pieces == 0:
            withheld = self._withhold_all(
                names,
                "this inventory has no regular parts: the set exists, but its "
                "inventory holds only spares or minifig parts",
            )
        elif inventory.geometryPieces == 0:
            withheld = self._withhold_all(
                names,
                "no part in this inventory has LDraw geometry, so nothing can "
                "be measured or placed",
            )
        else:
            lots = self._cat.combine(*resolved)
            for name in names:
                template = TEMPLATES[name]
                for variant in template.variants:
                    record, held = self._try_build(template, variant, resolved, lots)
                    if record is not None:
                        records.append(record)
                        designs.append(record.design)
                    if held is not None:
                        withheld.append(held)

        # Contract: best first, by piece count descending. The id breaks ties so
        # two designs of equal size never swap order between requests.
        designs.sort(key=lambda design: (-design.pieceCount, design.id))
        response = GenerateResponse(
            inventory=inventory,
            designs=designs,
            withheld=withheld,
            elapsedMs=_ms_since(started),
        )
        for record in records:
            self._register(record)
        self._generated[key] = (response, tuple(records))
        self._trim(self._generated, GENERATE_CACHE_SIZE)
        return response

    def design_ldr(self, design_id: str) -> DesignRecord:
        return self._registered(design_id)

    def design_export(self, design_id: str) -> DesignExport:
        """The downloadable package for a design, rendered at most once.

        Cached by design id, because the whole cost is in the drawing: a
        236-part building is one to two seconds of it, and the zip, the
        finished-model image and every step image all come out of one pass. Serving any of the three therefore warms the other two, and
        rebuilding per request - or per image - would be paying that cost over
        and over for bytes that cannot have changed. They cannot change because
        a design id is a hash of (archetype, variant, setNums) and the package
        is built without a clock in it.
        """
        record = self._registered(design_id)
        cached = self._exports.get(design_id)
        if cached is not None:
            self._exports.move_to_end(design_id)
            return cached
        try:
            export = build_export(
                export_spec(record, self._colour_rgb), record.build, self._renderer
            )
        except RenderFailed as exc:
            # Three separate outcomes, deliberately: an unknown id is the 404
            # above, a build the renderer cannot draw is this, and a document
            # or archive that will not assemble is the next clause. Collapsing
            # them into one 500 would send whoever reads the log looking in the
            # wrong module.
            logger.exception("render failed for design %s", design_id)
            raise ApiError(
                500, RENDER_FAILED, "this design could not be drawn: " + str(exc)
            ) from exc
        except PackageFailed as exc:
            logger.exception("packaging failed for design %s", design_id)
            raise ApiError(
                500, PACKAGE_FAILED, "this design could not be packaged: " + str(exc)
            ) from exc
        self._exports[design_id] = export
        self._trim(self._exports, EXPORT_CACHE_SIZE)
        logger.info(
            "packaged design %s: %d steps, %d bytes",
            design_id,
            len(export.images.steps),
            export.size,
        )
        return export

    def design_image(self, design_id: str) -> bytes:
        return self.design_export(design_id).images.model

    def design_step_image(self, design_id: str, index: int) -> bytes:
        """One step's PNG, ghosting everything placed before it.

        An index outside the design is `step_not_found` rather than
        `invalid_request`: 1-based indices are the contract's, and asking for
        step 40 of an 8-step design is a wrong question about a real design,
        not a malformed request.
        """
        steps = self.design_export(design_id).images.steps
        if not 1 <= index <= len(steps):
            raise ApiError(
                404,
                STEP_NOT_FOUND,
                f"this design has {len(steps)} steps, so there is no step {index}",
            )
        return steps[index - 1]

    def _registered(self, design_id: str) -> DesignRecord:
        record = self._designs.get(design_id)
        if record is None:
            raise ApiError(
                404,
                DESIGN_NOT_FOUND,
                "no such design; ids are not persisted across restarts, so "
                "generate that inventory again to recreate it",
            )
        self._designs.move_to_end(design_id)
        return record

    # --------------------------------------------------------------- helpers

    def _summary_from_row(self, row: sqlite3.Row) -> SetSummary:
        return SetSummary(
            setNum=row["set_num"],
            name=row["name"],
            year=row["year"],
            themeId=row["theme_id"],
            themeName=row["theme_name"],
            # The contract pins numParts to 0 when unknown, not to null.
            numParts=row["num_parts"] or 0,
            imgUrl=row["img_url"],
        )

    def _resolve_one(self, raw: str) -> str:
        return self._resolve_all([raw])[0]

    def _resolve_all(self, raw: Sequence[str]) -> tuple[str, ...]:
        """Validate, then canonicalise set numbers, or raise the right error.

        Malformed input is reported before unknown input, and separately: a
        client sending `"42151; DROP"` has a bug, while one sending
        `"99999999-1"` merely asked about a set that does not exist. The result
        is sorted, because an inventory is a multiset - order carries no
        meaning - and sorting is what makes the cache key canonical.
        Duplicates survive: two copies of a set really is twice the parts.
        """
        malformed = [value for value in raw if not SET_NUM_RE.match(value.strip())]
        if malformed:
            raise ApiError(
                400,
                INVALID_REQUEST,
                "one or more entries are not valid set numbers",
                {"malformed": malformed},
            )
        resolved: list[str] = []
        unresolved: list[str] = []
        for value in raw:
            canonical = self._cat.resolve_set(value.strip())
            if canonical is None:
                unresolved.append(value)
            else:
                resolved.append(canonical)
        if unresolved:
            raise ApiError(
                404,
                SET_NOT_FOUND,
                "one or more set numbers are not in the catalogue",
                {"unresolved": unresolved},
            )
        return tuple(sorted(resolved))

    def _select_archetypes(self, archetypes: Sequence[str] | None) -> list[str]:
        if archetypes is None:
            return list(TEMPLATES)
        unknown = [name for name in archetypes if name not in TEMPLATES]
        if unknown:
            raise ApiError(
                400,
                INVALID_REQUEST,
                "unknown archetype name",
                {"unknown": unknown, "known": list(TEMPLATES)},
            )
        # De-duplicated and kept in template declaration order, so the output
        # order does not depend on how the client happened to list them.
        wanted = set(archetypes)
        return [name for name in TEMPLATES if name in wanted]

    def _inventory_for(self, resolved: tuple[str, ...]) -> Inventory:
        cached = self._inventories.get(resolved)
        if cached is not None:
            self._inventories.move_to_end(resolved)
            return cached
        profile: Profile = self._taxonomy.profile(*resolved)
        families = [
            FamilyCount(
                family=family,
                pieces=pieces,
                studs=int(profile.area_by_family.get(family, 0)),
            )
            for family, pieces in profile.by_family.most_common()
            if pieces > 0
        ]
        inventory = Inventory(
            setNums=list(resolved),
            pieces=profile.pieces,
            lots=profile.lots,
            geometryPieces=profile.geometry_pieces,
            families=families,
            structuralStuds=profile.structural_area,
        )
        self._inventories[resolved] = inventory
        self._trim(self._inventories, INVENTORY_CACHE_SIZE)
        return inventory

    def _try_build(
        self,
        template: Template,
        variant: dict[str, Any],
        resolved: tuple[str, ...],
        lots: Counter,
    ) -> tuple[DesignRecord | None, WithheldDesign | None]:
        """One archetype/variant attempt: either a design, or a stated refusal."""
        variant_name: str | None = variant.get("name")
        try:
            build = self._fitter.fit(template, lots, variant)
        except Exception as exc:  # noqa: BLE001 - one archetype must not sink the request
            # A layout raising is an engine bug, not a client error, so it is
            # logged with a traceback and reported as a withheld design; the
            # other archetypes still get their chance at this inventory.
            logger.exception(
                "archetype %s/%s raised on %s", template.name, variant_name, resolved
            )
            return None, WithheldDesign(
                archetype=template.name,
                variant=variant_name,
                reason="generation failed inside the engine: " + type(exc).__name__,
            )

        if not build.buildable or build.confidence < CONFIDENCE_FLOOR:
            return None, WithheldDesign(
                archetype=template.name,
                variant=variant_name,
                reason=_withheld_reason(build),
            )
        return self._record(template, variant_name, resolved, build), None

    def _record(
        self,
        template: Template,
        variant_name: str | None,
        resolved: tuple[str, ...],
        build: Build,
    ) -> DesignRecord:
        design_id = design_identity(template.name, variant_name, resolved)
        title = template.title if not variant_name else f"{template.title} ({variant_name})"
        slug = template.name + ("-" + variant_name if variant_name else "")
        filename = f"{slug}_{'_'.join(resolved)}.ldr"
        design = Design(
            id=design_id,
            archetype=template.name,
            variant=variant_name,
            title=title,
            pieceCount=build.piece_count,
            confidence=build.confidence,
            warnings=list(build.warnings),
            # Translated once, here, and published: the web page and the PDF
            # both render this field verbatim (CONTRACT.md v1.2). Anything the
            # translation does not recognise is dropped rather than shown, so
            # `warnings` stays the only place raw diagnostics appear.
            builderNotes=list(builder_notes(build.warnings)),
            steps=self._steps(build),
            # Relative on purpose: the service does not know the public origin
            # it is reached through, and the client resolves it against the base
            # URL it already used for this request.
            ldrUrl=f"/api/v1/designs/{design_id}/ldr",
        )
        return DesignRecord(
            design=design,
            # Rendered through the engine's own writer, which is the single
            # place that knows the model header - including the Rebrickable and
            # LDraw attribution and the LEGO non-affiliation notice PRD R4/R5
            # require of anything shipped. The file name is passed because it
            # goes into the model's `0 Name:` line, so a kept file names itself
            # as it was downloaded.
            ldr=self._fitter.ldr_text(build, title, filename),
            filename=filename,
            build=build,
            # Carried because the package names the inventory it was generated
            # from, on the cover and in ATTRIBUTION.txt. `Design` has no field
            # for it, and the file name is not a data structure to parse.
            set_nums=resolved,
        )

    def _steps(self, build: Build) -> list[BuildStep]:
        """Group placements into steps by level - the order a human builds in.

        Mirrors `Fitter.instructions()`, which produces the same grouping as
        text. It is reimplemented rather than parsed because the contract needs
        the parts itemised as data, and parsing a display string back into
        fields would break the moment that string is reformatted.
        """
        by_level: dict[float, list[PlacedPart]] = {}
        for part in build.parts:
            by_level.setdefault(float(part.level), []).append(part)

        steps: list[BuildStep] = []
        for index, level in enumerate(sorted(by_level), start=1):
            tally: dict[tuple[str, int, str], int] = {}
            for part in by_level[level]:
                key = (part.part_num, part.color_id, part.note or part.role)
                tally[key] = tally.get(key, 0) + 1
            parts = [
                StepPart(
                    partNum=part_num,
                    partName=self._part_names.get(part_num, "unknown part"),
                    colorId=colour_id,
                    colorName=self._colour_names.get(colour_id, "[Unknown]"),
                    quantity=quantity,
                    note=note,
                )
                for (part_num, colour_id, note), quantity in tally.items()
            ]
            parts.sort(key=lambda part: (-part.quantity, part.partNum, part.colorId))
            steps.append(BuildStep(index=index, level=level, parts=parts))
        return steps

    def _register(self, record: DesignRecord) -> None:
        self._designs[record.design.id] = record
        self._designs.move_to_end(record.design.id)
        self._trim(self._designs, DESIGN_REGISTRY_SIZE)

    @staticmethod
    def _trim(cache: OrderedDict[Any, Any], limit: int) -> None:
        """Evict least-recently-used entries.

        The design registry is in-process only, which the contract explicitly
        allows ("ids are not persisted across restarts"), so eviction is a 404
        on a stale id rather than a correctness problem.
        """
        while len(cache) > limit:
            cache.popitem(last=False)

    def _withhold_all(self, names: Sequence[str], reason: str) -> list[WithheldDesign]:
        """One stated refusal per archetype/variant, without running the fitter.

        Used when the inventory cannot support any archetype at all. Running
        every template would reach the same conclusion with a much vaguer
        reason ("only 0 pieces") and spend seconds saying it.
        """
        out: list[WithheldDesign] = []
        for name in names:
            for variant in TEMPLATES[name].variants:
                out.append(
                    WithheldDesign(
                        archetype=name, variant=variant.get("name"), reason=reason
                    )
                )
        return out


def _withheld_reason(build: Build) -> str:
    """Why a build is not being offered, in words a person can act on."""
    if build.unfilled:
        return "the inventory is missing a required part: " + build.unfilled[0]
    if build.collisions:
        return "parts would intersect: " + build.collisions[0]
    if build.unsupported:
        return "a part would float unsupported: " + build.unsupported[0]
    if build.piece_count < MIN_PIECES:
        return (
            f"only {build.piece_count} pieces could be placed; a design needs "
            f"at least {MIN_PIECES}"
        )
    if notes := list(getattr(build, "weak", None) or []):
        # A weak placement validates but is precarious, and the engine counts it
        # as unbuildable precisely so the contract's "confidence is always 1.0"
        # stays true. Read through getattr so this service keeps working if the
        # engine renames the field.
        return "a part would be precariously supported: " + notes[0]
    if build.confidence < CONFIDENCE_FLOOR:
        return (
            f"validated at confidence {build.confidence:.3f}, below the 1.0 "
            f"the API guarantees for anything it offers"
        )
    return "the archetype produced no placements for this inventory"


class EngineHandle:
    """Async front door to the engine, and owner of its single worker thread.

    `max_workers=1` is the whole point, not a tuning choice: the pool's one
    thread constructs the engine and then performs every call against it, which
    is what makes the thread-bound SQLite connection and the shared zip handle
    safe (see the module docstring). A `ThreadPoolExecutor` never retires an
    idle worker, so that thread stays the same thread for the life of the
    process.
    """

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mocforge-engine"
        )
        self._engine: Engine | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._engine = await loop.run_in_executor(self._pool, Engine.load)

    async def stop(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._pool, engine.close)
        self._pool.shutdown(wait=True)

    @property
    def engine(self) -> Engine:
        if self._engine is None:  # pragma: no cover - the lifespan guarantees this
            raise ApiError(
                503, SERVICE_UNAVAILABLE, "the generation engine is not loaded"
            )
        return self._engine

    async def run(self, work: Callable[[Engine], T]) -> T:
        """Await `work(engine)` on the owner thread."""
        engine = self.engine
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, work, engine)

    def snapshot_health(self) -> HealthResponse:
        """Read the startup snapshot directly, without queueing on the worker.

        A liveness probe that blocks behind a ten-second generation is useless
        for the one thing it exists to answer. This snapshot and the archetype
        list are immutable after startup, so reading them off-thread is safe.
        """
        return self.engine.health()

    def snapshot_archetypes(self) -> ArchetypesResponse:
        return self.engine.archetypes()
