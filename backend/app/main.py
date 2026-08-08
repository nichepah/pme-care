"""Application entry point.

Kept deliberately small: one middleware (request id + timing + security
headers + one access-log line), the error handlers for the standard
envelope, CORS, and router wiring.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import AppError, init_firebase
from app.config import settings
from app.context import client_ip_var, extract_client_ip, request_id_var
from app.routes.audit import router as audit_router
from app.routes.employees import router as employees_router
from app.routes.examinations import router as examinations_router
from app.routes.system import router as system_router
from app.routes.users import router as users_router

logger = logging.getLogger("pme")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _error_body(code: str, message: str, details: list[dict]) -> dict:
    """Build the standard error envelope (API spec §1.5)."""
    return {"error": {"code": code, "message": message,
                      "details": details, "request_id": request_id_var.get()}}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: logging level + Firebase Admin initialization."""
    logging.basicConfig(level="INFO" if settings.is_production else "DEBUG",
                        format="%(levelname)s %(name)s %(message)s")
    init_firebase()
    yield


def create_app() -> FastAPI:
    """Build and wire the FastAPI application."""
    # In production all three of these are off: serving the OpenAPI schema
    # while hiding the Swagger page only hides the UI — the schema is the part
    # that enumerates every route and field. Generate clients from a
    # non-production deployment instead.
    app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION,
                  lifespan=lifespan,
                  docs_url=None if settings.is_production else "/docs",
                  openapi_url=None if settings.is_production else "/openapi.json",
                  redoc_url=None)

    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins_list,
                       allow_credentials=False, allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type", "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Stamp a request id, add security headers, log one line per request."""
        rid = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:8]}"
        request_id_var.set(rid)
        client_ip_var.set(extract_client_ip(request))
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        logger.info("%s %s %s %.0fms rid=%s", request.method, request.url.path,
                    response.status_code, (time.perf_counter() - started) * 1000, rid)
        return response

    @app.exception_handler(AppError)
    async def on_app_error(_: Request, exc: AppError) -> JSONResponse:
        """Domain errors → standard envelope with their status code."""
        return JSONResponse(status_code=exc.status_code,
                            content=_error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def on_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Pydantic request failures → 400 VALIDATION_ERROR."""
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or str(e["loc"][0]),
                    "issue": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=400,
                            content=_error_body("VALIDATION_ERROR",
                                                "Request validation failed.", details))

    @app.exception_handler(Exception)
    async def on_unexpected(_: Request, exc: Exception) -> JSONResponse:
        """Catch-all: log the stack, leak nothing."""
        logger.exception("Unhandled error rid=%s", request_id_var.get())
        return JSONResponse(status_code=500,
                            content=_error_body("INTERNAL_ERROR",
                                                "An internal error occurred.", []))

    app.include_router(system_router, prefix=settings.API_PREFIX)
    app.include_router(users_router, prefix=settings.API_PREFIX)
    app.include_router(employees_router, prefix=settings.API_PREFIX)
    app.include_router(examinations_router, prefix=settings.API_PREFIX)
    app.include_router(audit_router, prefix=settings.API_PREFIX)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the SPA from this process, if its directory is present.

    Same-origin serving means no CORS at all — no preflights, no allowlist to keep
    in step with wherever the UI is hosted, and no chance of a deployment that
    works until someone opens it on a different hostname. For a single local
    server, which is how this is deployed, one process serving both is also one
    fewer thing to start and supervise.

    Mounted last so it can never shadow an API route, and skipped silently when
    the directory is absent — an API-only deployment is a valid configuration.
    """
    directory = Path(settings.FRONTEND_DIR)
    if not directory.is_dir():
        logger.info("No frontend at %s; serving the API only.", directory)
        return
    # html=True serves index.html for "/", which is all the hash-routed SPA needs.
    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")
    logger.info("Serving the frontend from %s", directory)


app = create_app()
