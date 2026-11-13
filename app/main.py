import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.deps import get_market_data_provider
from app.api.errors import register_exception_handlers
from app.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.api.pagination import TOTAL_COUNT_HEADER
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, configure_sentry
from app.db.session import AsyncSessionLocal, engine
from app.worker import Worker

configure_logging()
configure_sentry()


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker: Worker | None = None
    worker_task: asyncio.Task | None = None
    if settings.worker_embedded:
        worker = Worker(AsyncSessionLocal, get_market_data_provider())
        worker_task = asyncio.create_task(worker.run_forever())
    yield
    if worker is not None and worker_task is not None:
        worker.stop()
        await worker_task
    await engine.dispose()


def operation_id(route: APIRoute) -> str:
    """Use the endpoint function name, so generated API clients get readable names."""
    return route.name


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
    generate_unique_id_function=operation_id,
)

# Added last means outermost: the request id exists before anything else runs.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    expose_headers=[TOTAL_COUNT_HEADER, "X-Request-ID", "Retry-After"],
)
app.add_middleware(RequestContextMiddleware)

register_exception_handlers(app)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", tags=["root"])
async def root():
    return {"name": settings.app_name, "version": "0.1.0", "docs": "/docs"}
