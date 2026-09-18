"""Error shape and the handlers that force every failure through it.

CONTRACT.md: "All errors use the `Error` shape with a correct HTTP status."
That includes failures FastAPI and Starlette would otherwise answer in their
own shapes - request validation (422 `{"detail": [...]}`), an unmatched route
(404 `{"detail": "Not Found"}`) and an unhandled exception (a bare 500) - so
each of those is re-rendered here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("mocforge.api")

# Codes used by the service. The contract names invalid_request,
# set_not_found, design_not_found and step_not_found; the rest cover failures
# it does not enumerate but which still have to arrive in the Error shape.
INVALID_REQUEST = "invalid_request"
SET_NOT_FOUND = "set_not_found"
DESIGN_NOT_FOUND = "design_not_found"
STEP_NOT_FOUND = "step_not_found"
NOT_FOUND = "not_found"
METHOD_NOT_ALLOWED = "method_not_allowed"
INTERNAL_ERROR = "internal_error"
SERVICE_UNAVAILABLE = "service_unavailable"

# Export failures. Two codes rather than one because they fail in unrelated
# places - the renderer could not draw the build, or the document and archive
# could not be assembled from images that drew fine - and a single
# `export_failed` would make the log the only way to tell them apart.
RENDER_FAILED = "render_failed"
PACKAGE_FAILED = "package_failed"


class ApiError(Exception):
    """An error with a contract code and the HTTP status that goes with it."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail

    def __repr__(self) -> str:
        return f"ApiError({self.status}, {self.code!r}, {self.message!r})"


def error_response(
    status: int, code: str, message: str, detail: Any | None = None
) -> JSONResponse:
    """Render the contract's `Error`, omitting `detail` when there is none.

    `detail?: unknown` in the contract means the key is absent, not null, so it
    is built as a plain dict here instead of through a model with a nullable
    field.
    """
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = jsonable_encoder(detail)
    return JSONResponse(status_code=status, content={"error": body})


def install(app: FastAPI) -> None:
    """Register the handlers. Called once by the app factory."""

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default is 422. The contract specifies 400 for a body that
        # is empty or over-long, and rule 6 makes an unknown field a rejection
        # rather than a 422 quirk, so every schema violation is one 400 with
        # the offending locations passed through as `detail`.
        return error_response(
            400,
            INVALID_REQUEST,
            "request does not match the API contract",
            {"errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            404: NOT_FOUND,
            405: METHOD_NOT_ALLOWED,
            503: SERVICE_UNAVAILABLE,
        }.get(exc.status_code, f"http_{exc.status_code}")
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Logged with a traceback and answered with a generic message: the
        # cause belongs in the server log, not in a client payload.
        logger.exception("unhandled error serving %s %s", request.method, request.url.path)
        return error_response(
            500, INTERNAL_ERROR, "the service failed to handle this request"
        )
