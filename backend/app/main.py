import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.services.autopilot import get_config as seed_autopilot
from app.data.groww import _load_persisted_token
from app.services.portfolio import ensure_seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

DESCRIPTION = """
AI stock research + paper-trading platform (Module 1: core backend).

**Paper trading only by default.** Signals are research output, not investment
advice; no returns are guaranteed. Data: Yahoo Finance (delayed). NSE symbols
use the `.NS` suffix, e.g. `RELIANCE.NS`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        ensure_seed(db)
        seed_autopilot(db)
    _load_persisted_token()
    from app.services.notify import _load_persisted as _load_tg
    _load_tg()
    from app.services.auto_learn import start_background as _start_bg
    _start_bg()
    log.info("%s ready — DB: %s | paper cash: %s %s",
             settings.app_name, settings.database_url.split("@")[-1],
             settings.base_currency, f"{settings.starting_cash:,.0f}")
    # Auto-trading loop disabled — this build is the Advisor + Scanner only.
    yield


app = FastAPI(title=settings.app_name, version="3.0.0", description=DESCRIPTION, lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your dashboard origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/ui", include_in_schema=False)
def ui_dashboard():
    """The self-configuring dashboard (index.html), wherever it was uploaded."""
    here = Path(__file__).parent
    candidates = (
        STATIC_DIR / "index.html",   # backend/app/static/index.html
        here / "index.html",         # backend/app/index.html
        here.parent / "index.html",  # backend/index.html
        Path.cwd() / "index.html",   # wherever the server was started from
    )
    for path in candidates:
        if path.exists():
            return FileResponse(path)
    return {"error": "index.html not found — upload it into the backend folder on GitHub"}


@app.get("/", include_in_schema=False)
def root():
    """The visual dashboard. API docs remain at /docs."""
    return FileResponse(STATIC_DIR / "dashboard.html")
