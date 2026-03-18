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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not required in production

from api.routes import router, do_refresh
from db import init_db, load_seed_if_empty

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_HOURS = 6


async def _background_refresh_loop():
    """Background task that refreshes data every 6 hours."""
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_HOURS * 3600)
        try:
            logger.info(f"=== SCHEDULED REFRESH (every {REFRESH_INTERVAL_HOURS}h) ===")
            result = await do_refresh(source="scheduled")
            logger.info(f"Scheduled refresh result: {result}")
        except Exception as e:
            logger.error(f"Scheduled refresh failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for FastAPI app."""
    logger.info("Starting GoalCast application")
    init_db()
    loaded = load_seed_if_empty()
    if loaded:
        logger.info(f"Seeded DB with {loaded} matches from seed.json")
    else:
        logger.info("Database initialized")

    # Run startup refresh in background (don't block server startup)
    async def _startup_refresh():
        await asyncio.sleep(3)  # Let the server finish starting
        try:
            if os.environ.get("API_FOOTBALL_KEY"):
                logger.info("=== STARTUP REFRESH ===")
                result = await do_refresh(source="startup")
                logger.info(f"Startup refresh result: {result}")
            else:
                logger.warning("No API_FOOTBALL_KEY set — skipping startup refresh")
        except Exception as e:
            logger.error(f"Startup refresh failed: {e}")

    startup_task = asyncio.create_task(_startup_refresh())
    refresh_task = asyncio.create_task(_background_refresh_loop())

    yield

    # Shutdown
    startup_task.cancel()
    refresh_task.cancel()
    logger.info("Shutting down GoalCast application")


# Create FastAPI app
app = FastAPI(
    title="GoalCast",
    description="AI-Powered Football Score Predictor",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(router)

# Mount static files
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/")
async def root():
    """Serve the main application page."""
    return FileResponse(static_path / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info(f"Starting uvicorn server on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
