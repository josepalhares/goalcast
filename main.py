"""FastAPI application entry point for GoalCast."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from api.routes import router, do_refresh
from api.auth import router as auth_router, setup_oauth
from db import init_db, load_seed_if_empty, get_db
from prediction.engine import load_xg_data, fit_model, set_dc_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting GoalCast application")

    # Fast synchronous startup — must complete in <5 seconds
    init_db()
    loaded = load_seed_if_empty()
    if loaded:
        logger.info(f"Seeded DB with {loaded} matches from seed.json")
    else:
        logger.info("Database initialized")

    load_xg_data()
    setup_oauth()
    logger.info("Server ready — accepting requests (model fitting in background)")

    # ALL slow work (model fitting + API refresh) runs in background
    async def _background_init():
        await asyncio.sleep(1)

        # Fit Dixon-Coles from seed data
        try:
            with get_db() as conn:
                finished = conn.execute("""
                    SELECT home_team, away_team, actual_home_goals, actual_away_goals, match_date, league
                    FROM matches WHERE status = 'finished' AND actual_home_goals IS NOT NULL
                """).fetchall()
            if finished:
                dc = fit_model([dict(r) for r in finished])
                set_dc_model(dc)
                logger.info(f"Dixon-Coles fitted from {len(finished)} matches")
        except Exception as e:
            logger.error(f"Model fitting failed: {e}")

        # API refresh
        await asyncio.sleep(2)
        try:
            if os.environ.get("FOOTBALL_DATA_KEY"):
                logger.info("=== STARTUP REFRESH ===")
                result = await do_refresh(source="startup")
                logger.info(f"Startup refresh result: {result}")
            else:
                logger.warning("No FOOTBALL_DATA_KEY set — skipping startup refresh")
        except Exception as e:
            logger.error(f"Startup refresh failed: {e}")

    bg_task = asyncio.create_task(_background_init())

    yield

    bg_task.cancel()
    logger.info("Shutting down GoalCast application")


app = FastAPI(
    title="GoalCast",
    description="AI-Powered Football Score Predictor",
    version="1.0.0",
    lifespan=lifespan
)

session_secret = os.environ.get("SESSION_SECRET", "goalcast-dev-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=session_secret, max_age=30 * 24 * 3600)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(router)

static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/")
async def root():
    return FileResponse(static_path / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting uvicorn server on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
