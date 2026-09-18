"""Pydantic models mirroring CONTRACT.md v1.

Field names are the contract's camelCase, written out literally instead of
being generated from snake_case by an alias generator. The contract is the
source of truth for a client built by a different team, so this file is meant
to be diffable against it by eye; a generator would hide a mismatch.

Every model forbids unknown fields, which contract rule 6 requires. On request
models that is load-bearing: a client that posts `setNum` where the contract
says `setNums` gets a 400 rather than a silently empty inventory. On response
models it is a development-time guard - a typo'd field name fails here, at the
server, instead of at a client that also rejects unknown fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    """Base for every contract shape: unknown fields are an error (rule 6)."""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------- requests


class NoQuery(Strict):
    """An endpoint that takes no query string at all.

    FastAPI ignores unexpected query parameters by default. Rule 5 says unknown
    query fields are rejected, so endpoints without parameters declare this
    empty strict model to get the same treatment as the ones with parameters.
    """


class SearchQuery(Strict):
    q: str = Field(min_length=1, description="Set number or name fragment.")
    limit: int = Field(default=20, ge=1, le=50)


class SetNumsRequest(Strict):
    """Body of POST /inventory. 1 to 10 set numbers.

    Bounds are declared here rather than checked in the service so that the
    contract's "400 on empty or >10" is enforced before any database work.
    Duplicates are deliberately allowed: two copies of one set is a real
    inventory, and the engine sums them.
    """

    setNums: list[str] = Field(min_length=1, max_length=10)


class GenerateRequest(SetNumsRequest):
    archetypes: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Restrict generation to these archetype names; null means all.",
    )
    # An explicit empty list is rejected rather than silently treated as "all":
    # it almost certainly means the client filtered its own list down to nothing
    # and would otherwise be told, misleadingly, that nothing is buildable.


# ---------------------------------------------------------------------- types


class SetSummary(Strict):
    setNum: str
    name: str
    year: int | None
    themeId: int | None
    themeName: str | None
    numParts: int
    imgUrl: str | None


class InventoryLot(Strict):
    """A single owned part lot.

    Declared because the contract declares it; no v1 endpoint returns one -
    `Inventory.lots` is a count, not a list. Kept so that the file stays a
    complete mirror of the contract and so an endpoint that later exposes lots
    does not invent a second shape.
    """

    partNum: str
    partName: str
    colorId: int
    colorName: str
    colorRgb: str | None
    isTrans: bool
    quantity: int
    imgUrl: str | None


class FamilyCount(Strict):
    family: str
    pieces: int
    studs: int


class Inventory(Strict):
    setNums: list[str]
    pieces: int
    lots: int
    geometryPieces: int
    families: list[FamilyCount]
    structuralStuds: int


class StepPart(Strict):
    partNum: str
    partName: str
    colorId: int
    colorName: str
    quantity: int
    note: str


class BuildStep(Strict):
    index: int = Field(ge=1)
    level: float
    parts: list[StepPart]


class Design(Strict):
    """One offered design.

    `warnings` and `builderNotes` are the same facts for two audiences, and
    both are needed. `warnings` is the engine's own diagnostic text - roles
    named by their match predicate, distances in LDU - which is what someone
    debugging the generator wants and is unfit to show a builder. `builderNotes`
    is the subset of it that means something to a person holding bricks, in
    words they can act on, produced by `export.builder_notes`. It arrived in
    v1.2 so that the page and the downloaded PDF render one server-side
    translation instead of each rewording the diagnostics themselves.
    """

    id: str
    archetype: str
    variant: str | None
    title: str
    pieceCount: int
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str]
    builderNotes: list[str]
    steps: list[BuildStep]
    ldrUrl: str


class WithheldDesign(Strict):
    archetype: str
    variant: str | None
    reason: str


# ------------------------------------------------------------------ responses


class CatalogueInfo(Strict):
    sets: int
    parts: int
    updatedAt: str


class HealthResponse(Strict):
    """Liveness, the catalogue snapshot, and what this build implements.

    `features` arrived with contract v1.1 and is the sanctioned way for a
    client to discover the image and package endpoints: the contract fixes
    `Design`'s fields, so a design cannot advertise its own extras, and a
    client is told to probe `/health` rather than to call an endpoint and read
    a 404 as "not built yet".
    """

    status: str
    catalogue: CatalogueInfo
    features: list[str]


class SearchResponse(Strict):
    results: list[SetSummary]


class GenerateResponse(Strict):
    inventory: Inventory
    designs: list[Design]
    withheld: list[WithheldDesign]
    elapsedMs: int


class ArchetypeInfo(Strict):
    name: str
    title: str
    variants: list[str]
    studded: bool


class ArchetypesResponse(Strict):
    archetypes: list[ArchetypeInfo]


class ErrorBody(Strict):
    code: str
    message: str
    detail: Any | None = None


class ErrorResponse(Strict):
    """The contract's `Error`, and the documented shape of every failure.

    Declared to FastAPI once, for all routes, so the generated schema says what
    the contract says. The bodies themselves are assembled by hand in
    errors.py, because `detail?: unknown` means the key is absent rather than
    null, and excluding unset fields globally would also drop the deliberate
    nulls elsewhere (`variant`, `year`, `themeId`) that the client relies on
    being present.
    """

    error: ErrorBody
