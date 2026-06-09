import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from planer.config import get_settings
from planer.logging_setup import configure_logging, get_logger, redact_path
from planer.web.admin import router as admin_router
from planer.web.public import limiter
from planer.web.public import router as public_router
from planer.web.tutor import router as tutor_router

# Safe default so any import-time / pre-startup logs are formatted; the real
# level/format from settings is applied in the lifespan startup below.
configure_logging()
_access_log = get_logger("access")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, json_format=settings.log_json)
    _access_log.info("startup", extra={"log_level": settings.log_level})
    yield


def docs_enabled() -> bool:
    """Whether to expose /docs, /redoc and /openapi.json.

    Read from the ENABLE_DOCS env var (not full Settings) so the FastAPI app can
    be built at import time without requiring SECRET_KEY. Default on for dev;
    set ENABLE_DOCS=false in production so the API schema (incl. admin route
    paths) is not publicly disclosed.
    """
    return os.environ.get("ENABLE_DOCS", "true").strip().lower() not in ("false", "0", "no")


_docs = docs_enabled()
app = FastAPI(
    title="Tutorium-Planer",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]


@app.middleware("http")
async def access_log(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Structured access log with magic-link tokens redacted from the path."""
    started = time.perf_counter()
    path = redact_path(request.url.path)
    client = request.client.host if request.client else "-"
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        _access_log.exception(
            "request failed",
            extra={
                "method": request.method,
                "path": path,
                "client": client,
                "duration_ms": duration_ms,
            },
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    level = (
        "error"
        if response.status_code >= 500
        else "warning"
        if response.status_code >= 400
        else "info"
    )
    getattr(_access_log, level)(
        "request",
        extra={
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "client": client,
            "duration_ms": duration_ms,
        },
    )
    return response


app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

app.include_router(public_router)
app.include_router(tutor_router)
app.include_router(admin_router)


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
