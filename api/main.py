"""MOCForge HTTP API - routes and app wiring.

Implements CONTRACT.md v1: base path `/api/v1`, the contract's shapes, and the
contract's status codes. Run it with:

    uvicorn api.main:app

Attribution (PRD R4/R5) travels on every response as headers rather than as
extra JSON fields. A response field would be compatible - rule 6 governs
requests - but the notice is the same on every response and belongs to the
service, not to any one design, so a header is where it goes and the contract's
shapes stay as CONTRACT.md declares them. The same notices are written inside
every generated .ldr file, and the client is separately required to show them
in its footer.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Path as PathParam, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from . import errors
from .engine import EngineHandle
from .models import (
    ArchetypesResponse,
    ErrorResponse,
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    Inventory,
    NoQuery,
    SearchQuery,
    SearchResponse,
    SetNumsRequest,
    SetSummary,
)

API_PREFIX = "/api/v1"

PNG_MEDIA_TYPE = "image/png"
ZIP_MEDIA_TYPE = "application/zip"

ATTRIBUTION = (
    "Catalogue data from Rebrickable. Geometry from the LDraw Parts Library, "
    "CC BY 2.0. LEGO is a trademark of the LEGO Group, which does not sponsor, "
    "authorise or endorse this project."
)

DESCRIPTION = (
    "Generates buildable LEGO MOCs from the sets a user owns, using only the "
    "local Rebrickable catalogue dump and the local LDraw parts library. "
    + ATTRIBUTION
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load the catalogue, the LDraw index and the fitter exactly once.

    Building them per request would mean opening the 240 MB SQLite database and
    re-indexing a 145 MB zip on every call, and would throw away the geometry
    cache that makes a second generation fast. Startup costs a couple of
    seconds; the process then serves every request from warm state.
    """
    handle = EngineHandle()
    await handle.start()
    app.state.engine = handle
    try:
        yield
    finally:
        await handle.stop()
        app.state.engine = None


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level constant so tests can hold their own
    instance; `app` below is the one uvicorn serves.
    """
    app = FastAPI(
        title="MOCForge API",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        # The contract is the specification, so the generated docs are a
        # convenience only; the default paths are kept.
        openapi_url="/openapi.json",
        # Declared once for every route: the contract says all errors use the
        # Error shape, so documenting it per route would be seven chances to
        # document it inconsistently.
        responses={
            400: {"model": ErrorResponse, "description": "Invalid request"},
            404: {"model": ErrorResponse, "description": "Not found"},
            500: {"model": ErrorResponse, "description": "Internal error"},
        },
    )
    errors.install(app)
    _align_schema_with_contract(app)

    # The web client is served from a different origin and calls this API from
    # the browser, so it needs CORS. This was invisible to the service's own
    # tests and to a Node-based integration test, because neither enforces the
    # same-origin policy - only a real browser does, and it refuses every call
    # (the preflight OPTIONS returned 405 before this was added).
    #
    # Explicit origins rather than "*": the allowlist is read from
    # MOCFORGE_CORS_ORIGINS (comma-separated) and defaults to the local dev
    # ports. A wildcard would be one line shorter and would also let any page
    # on the internet drive a service holding the user's inventory.
    origins = [
        o.strip()
        for o in os.environ.get(
            "MOCFORGE_CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        # No credentials are used: the API has no auth and no cookies, so
        # allowing them would widen exposure for no benefit.
        allow_credentials=False,
        expose_headers=["X-Data-Attribution"],
    )

    @app.middleware("http")
    async def attribution(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Data-Attribution"] = ATTRIBUTION
        return response

    def handle(request: Request) -> EngineHandle:
        return request.app.state.engine

    # ----------------------------------------------------------------- health

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse)
    async def health(
        request: Request, _query: Annotated[NoQuery, Query()]
    ) -> HealthResponse:
        # Answered from the startup snapshot, so a probe is never queued behind
        # a generation that takes seconds.
        return handle(request).snapshot_health()

    @app.get(f"{API_PREFIX}/archetypes", response_model=ArchetypesResponse)
    async def archetypes(
        request: Request, _query: Annotated[NoQuery, Query()]
    ) -> ArchetypesResponse:
        return handle(request).snapshot_archetypes()

    # ------------------------------------------------------------------- sets

    # Declared before `/sets/{setNum}`: FastAPI matches routes in declaration
    # order, and the other way round "search" is read as a set number and the
    # search endpoint becomes unreachable.
    @app.get(f"{API_PREFIX}/sets/search", response_model=SearchResponse)
    async def search_sets(
        request: Request, query: Annotated[SearchQuery, Query()]
    ) -> SearchResponse:
        results = await handle(request).run(
            lambda engine: engine.search(query.q, query.limit)
        )
        return SearchResponse(results=results)

    @app.get(f"{API_PREFIX}/sets/{{setNum}}", response_model=SetSummary)
    async def get_set(
        request: Request,
        setNum: Annotated[str, PathParam(min_length=1, max_length=64)],
        _query: Annotated[NoQuery, Query()],
    ) -> SetSummary:
        return await handle(request).run(lambda engine: engine.set_summary(setNum))

    # -------------------------------------------------------------- inventory

    @app.post(f"{API_PREFIX}/inventory", response_model=Inventory)
    async def post_inventory(request: Request, body: SetNumsRequest) -> Inventory:
        return await handle(request).run(lambda engine: engine.inventory(body.setNums))

    # --------------------------------------------------------------- generate

    @app.post(f"{API_PREFIX}/generate", response_model=GenerateResponse)
    async def post_generate(
        request: Request, body: GenerateRequest
    ) -> GenerateResponse:
        # Synchronous per contract behaviour requirement 1: this call can take
        # seconds on a cold inventory, and the client shows real progress for it.
        return await handle(request).run(
            lambda engine: engine.generate(body.setNums, body.archetypes)
        )

    # ---------------------------------------------------------------- designs

    @app.get(
        f"{API_PREFIX}/designs/{{designId}}/ldr",
        response_class=PlainTextResponse,
        responses={200: {"description": "LDraw model"}},
    )
    async def get_design_ldr(
        request: Request,
        designId: Annotated[str, PathParam(min_length=1, max_length=64)],
        _query: Annotated[NoQuery, Query()],
    ) -> PlainTextResponse:
        record = await handle(request).run(lambda engine: engine.design_ldr(designId))
        return PlainTextResponse(
            content=record.ldr,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{record.filename}"'
            },
        )

    # ------------------------------------------------------- designs, v1.1

    # The three routes below are the v1.1 additions CONTRACT.md reserved, plus
    # the package endpoint added in the same version. All three are answered
    # from one cached render pass per design (see `Engine.design_export`), so
    # whichever a client asks for first pays the cost and the rest are free.
    # They stay `async def` and await the engine's own thread, because drawing
    # a 236-part model in the event loop would stall every other request for
    # as long as it took.

    @app.get(
        f"{API_PREFIX}/designs/{{designId}}/image",
        responses={200: {"content": {PNG_MEDIA_TYPE: {}}, "description": "PNG render"}},
    )
    async def get_design_image(
        request: Request,
        designId: Annotated[str, PathParam(min_length=1, max_length=64)],
        _query: Annotated[NoQuery, Query()],
    ) -> Response:
        png = await handle(request).run(lambda engine: engine.design_image(designId))
        return Response(content=png, media_type=PNG_MEDIA_TYPE)

    @app.get(
        f"{API_PREFIX}/designs/{{designId}}/steps/{{index}}/image",
        responses={200: {"content": {PNG_MEDIA_TYPE: {}}, "description": "PNG render"}},
    )
    async def get_step_image(
        request: Request,
        designId: Annotated[str, PathParam(min_length=1, max_length=64)],
        # No lower bound is declared: an index of 0 or -3 is a wrong question
        # about a real design, which the engine answers `404 step_not_found`,
        # the same as an index past the end. Only a non-integer is malformed,
        # and that is the 400 this path parameter does enforce.
        index: Annotated[int, PathParam()],
        _query: Annotated[NoQuery, Query()],
    ) -> Response:
        png = await handle(request).run(
            lambda engine: engine.design_step_image(designId, index)
        )
        return Response(content=png, media_type=PNG_MEDIA_TYPE)

    @app.get(
        f"{API_PREFIX}/designs/{{designId}}/package",
        responses={
            200: {"content": {ZIP_MEDIA_TYPE: {}}, "description": "Instruction pack"}
        },
    )
    async def get_design_package(
        request: Request,
        designId: Annotated[str, PathParam(min_length=1, max_length=64)],
        _query: Annotated[NoQuery, Query()],
    ) -> Response:
        export = await handle(request).run(
            lambda engine: engine.design_export(designId)
        )
        return Response(
            content=export.zip_bytes,
            media_type=ZIP_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{export.filename}"'
            },
        )

    return app


def _align_schema_with_contract(app: FastAPI) -> None:
    """Correct two things FastAPI infers that this service does not do.

    The generated schema is a convenience next to CONTRACT.md, but it should
    not contradict it:

    * FastAPI documents a 422 on every route that takes parameters. This
      service never returns one - errors.py turns every schema violation into
      the contract's `400 invalid_request` - and documenting an impossible
      status invites the client team to handle it.
    * The LDraw endpoint's `response_class` makes FastAPI describe that route's
      error bodies as text/plain too. They are JSON, as the contract says of
      every response it does not explicitly call text.
    """
    generate_schema = app.openapi

    def openapi() -> dict[str, Any]:
        schema = generate_schema()
        error_ref = {"$ref": "#/components/schemas/ErrorResponse"}
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                responses: dict[str, Any] = operation.get("responses", {})
                responses.pop("422", None)
                for status, response in responses.items():
                    if not status.startswith("2"):
                        response["content"] = {"application/json": {"schema": error_ref}}
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


app = create_app()
