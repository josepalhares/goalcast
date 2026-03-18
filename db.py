"""SQLite database setup and helpers."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
import logging

logger = logging.getLogger(__name__)

DB_PATH = Path("data/goalcast.db")


def init_db() -> None:
    """Initialize the database with required tables."""
    DB_PATH.parent.mkdir(exist_ok=True)

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_match_id TEXT UNIQUE,
                league TEXT,
                home_team TEXT,
                away_team TEXT,
                match_date DATETIME,
                home_elo REAL,
                away_elo REAL,
                status TEXT DEFAULT 'upcoming',
                actual_home_goals INTEGER,
                actual_away_goals INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER REFERENCES matches(id),
                source TEXT,
                predicted_home_goals INTEGER,
                predicted_away_goals INTEGER,
                home_win_prob REAL,
                draw_prob REAL,
                away_win_prob REAL,
                confidence TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(match_id, source)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team_elo_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT,
                elo_rating REAL,
                fetched_date DATE,
                UNIQUE(team_name, fetched_date)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.commit()

    # Add confidence column if missing (migration for existing DBs)
    try:
        with get_db() as conn:
            conn.execute("SELECT confidence FROM predictions LIMIT 1")
    except sqlite3.OperationalError:
        with get_db() as conn:
            conn.execute("ALTER TABLE predictions ADD COLUMN confidence TEXT")
            conn.commit()
            logger.info("Added confidence column to predictions table")


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def upsert_match(
    api_match_id: str,
    league: str,
    home_team: str,
    away_team: str,
    match_date: str,
    home_elo: float,
    away_elo: float,
    status: str,
    actual_home_goals: Optional[int] = None,
    actual_away_goals: Optional[int] = None,
) -> int:
    """Insert or update a match. Returns the match row id."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO matches (api_match_id, league, home_team, away_team, match_date,
                                 home_elo, away_elo, status, actual_home_goals, actual_away_goals)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(api_match_id) DO UPDATE SET
                status = excluded.status,
                actual_home_goals = COALESCE(excluded.actual_home_goals, actual_home_goals),
                actual_away_goals = COALESCE(excluded.actual_away_goals, actual_away_goals),
                home_elo = excluded.home_elo,
                away_elo = excluded.away_elo
        """, (api_match_id, league, home_team, away_team, match_date,
              home_elo, away_elo, status, actual_home_goals, actual_away_goals))
        conn.commit()

        # Get the row id
        row = cursor.execute(
            "SELECT id FROM matches WHERE api_match_id = ?", (api_match_id,)
        ).fetchone()
        return row["id"]


def upsert_ai_prediction(
    match_id: int,
    predicted_home_goals: int,
    predicted_away_goals: int,
    home_win_prob: float,
    draw_prob: float,
    away_win_prob: float,
    confidence: str,
) -> None:
    """Insert AI prediction only if one doesn't already exist for this match."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO predictions
                (match_id, source, predicted_home_goals, predicted_away_goals,
                 home_win_prob, draw_prob, away_win_prob, confidence)
            VALUES (?, 'ai', ?, ?, ?, ?, ?, ?)
        """, (match_id, predicted_home_goals, predicted_away_goals,
              home_win_prob, draw_prob, away_win_prob, confidence))
        conn.commit()


def get_all_matches_from_db() -> list[dict]:
    """Get all matches from DB with their AI predictions, sorted by date."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT m.*, p.predicted_home_goals, p.predicted_away_goals,
                   p.home_win_prob, p.draw_prob, p.away_win_prob, p.confidence
            FROM matches m
            LEFT JOIN predictions p ON p.match_id = m.id AND p.source = 'ai'
            ORDER BY m.match_date ASC
        """).fetchall()

    return [dict(r) for r in rows]


def get_match_count() -> int:
    """Get total number of matches in the DB."""
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM matches").fetchone()
        return row["cnt"]


SEED_PATH = Path(__file__).parent / "data" / "seed.json"


def load_seed_if_empty() -> int:
    """If DB has 0 matches, load from seed.json. Returns number of matches loaded."""
    if get_match_count() > 0:
        return 0

    if not SEED_PATH.exists():
        logger.info("No seed.json found, starting with empty DB")
        return 0

    logger.info(f"DB is empty — loading seed from {SEED_PATH}")
    try:
        seed = json.loads(SEED_PATH.read_text())
    except Exception as e:
        logger.error(f"Failed to read seed.json: {e}")
        return 0

    loaded = 0
    with get_db() as conn:
        cursor = conn.cursor()
        for m in seed.get("matches", []):
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO matches
                        (api_match_id, league, home_team, away_team, match_date,
                         home_elo, away_elo, status, actual_home_goals, actual_away_goals)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (m["api_match_id"], m["league"], m["home_team"], m["away_team"],
                      m["match_date"], m["home_elo"], m["away_elo"], m["status"],
                      m.get("actual_home_goals"), m.get("actual_away_goals")))
                if cursor.rowcount > 0:
                    loaded += 1
                    # Load prediction if present
                    p = m.get("prediction")
                    if p:
                        match_id = cursor.execute(
                            "SELECT id FROM matches WHERE api_match_id = ?",
                            (m["api_match_id"],)
                        ).fetchone()["id"]
                        cursor.execute("""
                            INSERT OR IGNORE INTO predictions
                                (match_id, source, predicted_home_goals, predicted_away_goals,
                                 home_win_prob, draw_prob, away_win_prob, confidence)
                            VALUES (?, 'ai', ?, ?, ?, ?, ?, ?)
                        """, (match_id, p["predicted_home_goals"], p["predicted_away_goals"],
                              p["home_win_prob"], p["draw_prob"], p["away_win_prob"],
                              p.get("confidence", "medium")))
            except Exception as e:
                logger.error(f"Seed import error for {m.get('api_match_id')}: {e}")
        conn.commit()

    logger.info(f"Loaded {loaded} matches from seed.json")
    return loaded


def export_db_to_dict() -> dict:
    """Export all matches + predictions as a JSON-serializable dict."""
    rows = get_all_matches_from_db()
    matches = []
    for r in rows:
        m = {
            "api_match_id": r["api_match_id"],
            "league": r["league"],
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "match_date": r["match_date"],
            "home_elo": r["home_elo"],
            "away_elo": r["away_elo"],
            "status": r["status"],
            "actual_home_goals": r["actual_home_goals"],
            "actual_away_goals": r["actual_away_goals"],
        }
        if r.get("predicted_home_goals") is not None:
            m["prediction"] = {
                "predicted_home_goals": r["predicted_home_goals"],
                "predicted_away_goals": r["predicted_away_goals"],
                "home_win_prob": r["home_win_prob"],
                "draw_prob": r["draw_prob"],
                "away_win_prob": r["away_win_prob"],
                "confidence": r.get("confidence"),
            }
        matches.append(m)
    return {"matches": matches, "exported_at": get_last_refresh() or "unknown"}


def save_seed_file() -> str:
    """Export DB and write to seed.json. Returns path."""
    data = export_db_to_dict()
    SEED_PATH.parent.mkdir(exist_ok=True)
    SEED_PATH.write_text(json.dumps(data, indent=2))
    logger.info(f"Saved {len(data['matches'])} matches to {SEED_PATH}")
    return str(SEED_PATH)


def get_last_refresh() -> Optional[str]:
    """Get the last refresh timestamp (ISO format string or None)."""
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'last_refresh'").fetchone()
        return row["value"] if row else None


def set_last_refresh(timestamp: str) -> None:
    """Store the last refresh timestamp."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('last_refresh', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (timestamp,)
        )
        conn.commit()
