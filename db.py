"""SQLite database setup and helpers."""
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
