"""FastAPI Main Application Entry Point."""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from jubran.settings import settings
from jubran.infrastructure.db.session import get_db_session, engine

logger = logging.getLogger("jubran")


def keep_log_file() -> None:
    """The server's notes (INFO and up) also go to LOG_FILE, kept small (a few rotating files)."""
    if not settings.LOG_FILE:
        return
    from logging.handlers import RotatingFileHandler
    from pathlib import Path
    path = Path(settings.LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        return
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    # Warnings still show in the server's window too.
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    logger.addHandler(console)
    logger.setLevel(logging.INFO)


keep_log_file()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from jubran.settings import ENV_FILES, env_file_conflicts
    conflicts = env_file_conflicts()
    if conflicts:
        logger.warning("These settings differ between %s and %s; the second (project root) is used: %s",
                       ENV_FILES[0], ENV_FILES[1], ", ".join(conflicts))

    # Database schema: apply pending Alembic migrations (once, even with several workers).
    from jubran.infrastructure.db.migrations.runner import migrate_database
    await migrate_database(engine)

    # Idempotently seed authoritative initial demo data if needed
    from jubran.infrastructure.db.session import async_session_factory
    from jubran.infrastructure.db.seed import seed_database
    async with async_session_factory() as session:
        try:
            await seed_database(session, with_photos=True)
            from jubran.application.ai.semantic_retrieval import SemanticKnowledgeService
            await SemanticKnowledgeService.ensure_fresh(session, force=True)
        except Exception as e:
            print("Auto-seed / semantic-index warning:", e)

    # Dish photos kept inside the database by older versions move out to files.
    from jubran.application.media_migration import move_photos_to_files
    async with async_session_factory() as session:
        try:
            await move_photos_to_files(session)
        except Exception as exc:  # photos stay readable from the database meanwhile
            logger.warning("Could not move dish photos to files yet: %s", exc)

    # After a data-encryption key rotation, stored secrets move to the new key.
    from jubran.application.secret_rotation import reencrypt_stored_secrets
    async with async_session_factory() as session:
        await reencrypt_stored_secrets(session)

    # Account safety is not optional: in production this refuses to start while
    # demo passwords would work or no administrator exists.
    from jubran.application.account_security import secure_accounts_on_startup
    async with async_session_factory() as session:
        await secure_accounts_on_startup(session)

    # Housekeeping now and every few minutes: expired records, tables nobody closed.
    from jubran.application.maintenance import maintenance_loop
    # Live updates: deliver committed events to this worker's WebSockets.
    from jubran.interfaces.websocket.gateway import ws_manager
    from jubran.interfaces.websocket.relay import EventRelay
    background = [
        asyncio.create_task(maintenance_loop(async_session_factory)),
        asyncio.create_task(EventRelay(async_session_factory, ws_manager).run()),
    ]
    if settings.PUBLIC_DEMO:
        # A copy anyone can try: a pretend kitchen prepares the visitors' orders.
        from jubran.application.public_demo import demo_kitchen_loop
        background.append(asyncio.create_task(demo_kitchen_loop(async_session_factory)))

    yield
    for task in background:
        task.cancel()
    for task in background:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await engine.dispose()


app = FastAPI(
    title="Jubran Restaurant API",
    description="Backend API for Jubran AI Restaurant Application",
    version="1.0.0",
    # API explorer and schema only while debugging; production exposes neither.
    docs_url="/docs" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
    redoc_url=None,
    lifespan=lifespan
)

# Middleware order (last added = outermost):
#   CORS → security headers → request context (ids + JSON 500) → CSRF → body size limit → app.
# CORS stays outermost so the web app can read every error, including an
# unexpected 500, and every rejection still carries the security headers.
from jubran.interfaces.http.body_limits import BodySizeLimitMiddleware
from jubran.interfaces.http.csrf import CSRFMiddleware
from jubran.interfaces.http.errors import RequestContextMiddleware, register_error_handlers
from jubran.interfaces.http.security_headers import SecurityHeadersMiddleware

app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
register_error_handlers(app)

# CORS (the same allowed origins also gate the live-update WebSockets)
from jubran.interfaces.http.origins import allowed_origin_regex

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=allowed_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include Routers
from jubran.interfaces.http.routers import auth, table_sessions, admin, menu, draft_and_orders, floor, customer_service, assistant, ai_models, public_demo
from jubran.interfaces.websocket import routes as websocket_routes

from jubran.application.rate_limiter import RateLimitExceeded
from jubran.interfaces.http.rate_limits import rate_limited_handler

app.add_exception_handler(RateLimitExceeded, rate_limited_handler)

app.include_router(auth.router)
app.include_router(table_sessions.router)
app.include_router(admin.router)
app.include_router(menu.router)
app.include_router(draft_and_orders.router)
app.include_router(floor.router)
app.include_router(customer_service.router)
app.include_router(assistant.router)
app.include_router(ai_models.router)
app.include_router(public_demo.router)
app.include_router(websocket_routes.router)


@app.get("/health", tags=["Health"])
async def root_health():
    return {"status": "ok", "service": "jubran_api"}


@app.get("/api/v1/health", tags=["Health"])
async def v1_health(db: AsyncSession = Depends(get_db_session)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        # Details (hosts, users, driver messages) go to the server log, never to visitors.
        logger.exception("Health check: database unavailable")
        db_status = "unhealthy"
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "environment": settings.ENVIRONMENT
    }
