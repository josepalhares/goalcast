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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                name TEXT,
                picture_url TEXT,
                role TEXT DEFAULT 'user',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS allowed_emails (
                email TEXT PRIMARY KEY
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                name TEXT,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        """)

        # Seed admin emails
        for email in ['jose.palhares@zendesk.com', 'josepalhares@gmail.com', 'josepalhares@hotmail.com']:
            cursor.execute("INSERT OR IGNORE INTO allowed_emails (email) VALUES (?)", (email,))

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

    # Add user_id column if missing
    try:
        with get_db() as conn:
            conn.execute("SELECT user_id FROM predictions LIMIT 1")
    except sqlite3.OperationalError:
        with get_db() as conn:
            conn.execute("ALTER TABLE predictions ADD COLUMN user_id INTEGER")
            conn.commit()
            logger.info("Added user_id column to predictions table")

    # Add last_login column to users if missing
    try:
        with get_db() as conn:
            conn.execute("SELECT last_login FROM users LIMIT 1")
    except sqlite3.OperationalError:
        with get_db() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN last_login DATETIME")
            conn.commit()
            logger.info("Added last_login column to users table")


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
    """Insert new match or update existing. Never deletes. Never regresses status.
    Returns the match row id."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Check if match exists
        existing = cursor.execute(
            "SELECT id, status FROM matches WHERE api_match_id = ?", (api_match_id,)
        ).fetchone()

        if existing is None:
            # Check for fuzzy duplicate: same date, similar team names (different API source)
            match_day = match_date[:10]  # Just the date part
            possible_dupes = cursor.execute(
                "SELECT id FROM matches WHERE match_date LIKE ? AND id != 0",
                (match_day + "%",)
            ).fetchall()
            if possible_dupes:
                ht_lower = home_team.lower()
                at_lower = away_team.lower()
                for row in possible_dupes:
                    dupe = cursor.execute(
                        "SELECT id, home_team, away_team FROM matches WHERE id = ?",
                        (row["id"],)
                    ).fetchone()
                    dh = dupe["home_team"].lower()
                    da = dupe["away_team"].lower()
                    # Check if one name contains the other (e.g. "Bournemouth" in "AFC Bournemouth")
                    home_match = ht_lower in dh or dh in ht_lower or ht_lower == dh
                    away_match = at_lower in da or da in at_lower or at_lower == da
                    if home_match and away_match:
                        logger.info(f"Skipping duplicate: {home_team} vs {away_team} (matches existing {dupe['home_team']} vs {dupe['away_team']})")
                        return dupe["id"]

            # New match — insert
            cursor.execute("""
                INSERT INTO matches (api_match_id, league, home_team, away_team, match_date,
                                     home_elo, away_elo, status, actual_home_goals, actual_away_goals)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (api_match_id, league, home_team, away_team, match_date,
                  home_elo, away_elo, status, actual_home_goals, actual_away_goals))
            conn.commit()
            row_id = cursor.execute(
                "SELECT id FROM matches WHERE api_match_id = ?", (api_match_id,)
            ).fetchone()["id"]
            return row_id
        else:
            # Existing match — only update forward (upcoming → finished), never regress
            row_id = existing["id"]
            old_status = existing["status"]

            # Only advance status: upcoming → finished (never go backward)
            new_status = status if status == "finished" else old_status

            # Only set scores if we have them and they're not already set
            cursor.execute("""
                UPDATE matches SET
                    status = ?,
                    actual_home_goals = COALESCE(?, actual_home_goals),
                    actual_away_goals = COALESCE(?, actual_away_goals),
                    home_elo = ?
                WHERE id = ?
            """, (new_status, actual_home_goals, actual_away_goals, home_elo, row_id))
            conn.commit()
            return row_id


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


def save_user_prediction(match_api_id: str, user_id: int, home: int, away: int) -> bool:
    """Save or update a user prediction. Returns True if saved."""
    with get_db() as conn:
        # Find the match row ID
        row = conn.execute("SELECT id FROM matches WHERE api_match_id = ?", (match_api_id,)).fetchone()
        if not row:
            return False
        match_id = row["id"]

        # Upsert: delete old prediction for this user+match, then insert
        conn.execute(
            "DELETE FROM predictions WHERE match_id = ? AND source = 'user' AND user_id = ?",
            (match_id, user_id)
        )
        conn.execute("""
            INSERT INTO predictions (match_id, source, predicted_home_goals, predicted_away_goals,
                                     home_win_prob, draw_prob, away_win_prob, user_id)
            VALUES (?, 'user', ?, ?, 0, 0, 0, ?)
        """, (match_id, home, away, user_id))
        conn.commit()
        return True


def delete_user_prediction(match_api_id: str, user_id: int) -> bool:
    """Delete a user prediction. Returns True if deleted."""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM matches WHERE api_match_id = ?", (match_api_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            "DELETE FROM predictions WHERE match_id = ? AND source = 'user' AND user_id = ?",
            (row["id"], user_id)
        )
        conn.commit()
        return True


def get_user_predictions(user_id: int) -> dict:
    """Get all predictions for a user. Returns {api_match_id: {home, away}}."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT m.api_match_id, p.predicted_home_goals as home, p.predicted_away_goals as away
            FROM predictions p JOIN matches m ON m.id = p.match_id
            WHERE p.source = 'user' AND p.user_id = ?
        """, (user_id,)).fetchall()
    return {r["api_match_id"]: {"home": r["home"], "away": r["away"]} for r in rows}


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


def get_calibration() -> Optional[dict]:
    """Get model calibration adjustments from DB."""
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'calibration'").fetchone()
        if row:
            try:
                return json.loads(row["value"])
            except Exception:
                pass
    return None


def set_calibration(cal: dict) -> None:
    """Store model calibration adjustments."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('calibration', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(cal),)
        )
        conn.commit()


def calculate_calibration() -> Optional[dict]:
    """Calculate global + per-league calibration from finished matches."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT m.actual_home_goals, m.actual_away_goals,
                   p.predicted_home_goals, p.predicted_away_goals,
                   m.league
            FROM matches m
            JOIN predictions p ON p.match_id = m.id AND p.source = 'ai'
            WHERE m.status = 'finished' AND m.actual_home_goals IS NOT NULL
        """).fetchall()

    if len(rows) < 10:
        return None

    def _calc_bias(subset):
        n = len(subset)
        if n == 0:
            return None
        avg_act_h = sum(r["actual_home_goals"] for r in subset) / n
        avg_act_a = sum(r["actual_away_goals"] for r in subset) / n
        avg_pred_h = sum(r["predicted_home_goals"] for r in subset) / n
        avg_pred_a = sum(r["predicted_away_goals"] for r in subset) / n
        return {
            "matches": n,
            "home_bias": round(avg_act_h - avg_pred_h, 3),
            "away_bias": round(avg_act_a - avg_pred_a, 3),
            "avg_actual_home": round(avg_act_h, 2),
            "avg_actual_away": round(avg_act_a, 2),
            "avg_predicted_home": round(avg_pred_h, 2),
            "avg_predicted_away": round(avg_pred_a, 2),
        }

    # Global calibration
    n = len(rows)
    global_cal = _calc_bias(rows)
    actual_draws = sum(1 for r in rows if r["actual_home_goals"] == r["actual_away_goals"])
    pred_draws = sum(1 for r in rows if r["predicted_home_goals"] == r["predicted_away_goals"])

    # Per-league calibration (min 5 matches per league)
    from collections import defaultdict
    by_league = defaultdict(list)
    for r in rows:
        by_league[r["league"]].append(r)

    league_cal = {}
    for league, league_rows in sorted(by_league.items()):
        if len(league_rows) >= 5:
            lc = _calc_bias(league_rows)
            league_cal[league] = lc
            logger.info(
                f"  {league} ({lc['matches']}m): "
                f"home_bias={lc['home_bias']:+.2f}, away_bias={lc['away_bias']:+.2f}"
            )

    cal = {
        **global_cal,
        "draw_rate_actual": round(actual_draws / n, 3),
        "draw_rate_predicted": round(pred_draws / n, 3),
        "by_league": league_cal,
    }

    set_calibration(cal)
    logger.info(
        f"Model calibration updated ({n} matches): "
        f"home_bias={global_cal['home_bias']:+.2f}, away_bias={global_cal['away_bias']:+.2f}, "
        f"draws: {cal['draw_rate_actual']:.0%} actual vs {cal['draw_rate_predicted']:.0%} predicted"
    )
    return cal


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
                    match_id = cursor.execute(
                        "SELECT id FROM matches WHERE api_match_id = ?",
                        (m["api_match_id"],)
                    ).fetchone()["id"]
                    # Load AI prediction if present
                    p = m.get("prediction")
                    if p:
                        cursor.execute("""
                            INSERT OR IGNORE INTO predictions
                                (match_id, source, predicted_home_goals, predicted_away_goals,
                                 home_win_prob, draw_prob, away_win_prob, confidence)
                            VALUES (?, 'ai', ?, ?, ?, ?, ?, ?)
                        """, (match_id, p["predicted_home_goals"], p["predicted_away_goals"],
                              p["home_win_prob"], p["draw_prob"], p["away_win_prob"],
                              p.get("confidence", "medium")))
                    # Load user prediction if present
                    up = m.get("user_prediction")
                    if up:
                        cursor.execute("""
                            INSERT OR IGNORE INTO predictions
                                (match_id, source, predicted_home_goals, predicted_away_goals,
                                 home_win_prob, draw_prob, away_win_prob)
                            VALUES (?, 'user', ?, ?, 0, 0, 0)
                        """, (match_id, up["home"], up["away"]))
            except Exception as e:
                logger.error(f"Seed import error for {m.get('api_match_id')}: {e}")
        conn.commit()

    logger.info(f"Loaded {loaded} matches from seed.json")
    return loaded


def export_db_to_dict() -> dict:
    """Export all matches + AI predictions + user predictions as JSON."""
    rows = get_all_matches_from_db()

    # Also get user predictions
    user_preds = {}
    with get_db() as conn:
        ups = conn.execute("""
            SELECT match_id, predicted_home_goals, predicted_away_goals
            FROM predictions WHERE source = 'user'
        """).fetchall()
        for up in ups:
            user_preds[up["match_id"]] = {
                "home": up["predicted_home_goals"],
                "away": up["predicted_away_goals"],
            }

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
        # Include user prediction if exists
        up = user_preds.get(r["id"])
        if up:
            m["user_prediction"] = up
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


# ─── Access requests ──────────────────────────────────────────

def submit_access_request(email: str, name: str) -> str:
    """Submit a new access request. Returns 'created', 'pending', 'approved', or 'denied'."""
    email = email.lower()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT status FROM access_requests WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            return existing["status"]
        conn.execute(
            "INSERT INTO access_requests (email, name) VALUES (?, ?)", (email, name)
        )
        conn.commit()
        return "created"


def get_access_request(email: str) -> Optional[dict]:
    """Get a single access request by email."""
    email = email.lower()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM access_requests WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None


def get_pending_requests() -> list:
    """Get all pending access requests, oldest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM access_requests WHERE status = 'pending' ORDER BY requested_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_requests() -> list:
    """Get all access requests (all statuses), newest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM access_requests ORDER BY requested_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_users() -> list:
    """Get all registered users."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, name, role, created_at, last_login "
            "FROM users ORDER BY last_login DESC NULLS LAST, created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def approve_access_request(email: str) -> None:
    """Add email to whitelist and mark request as approved."""
    email = email.lower()
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO allowed_emails (email) VALUES (?)", (email,))
        conn.execute(
            "UPDATE access_requests SET status = 'approved' WHERE email = ?", (email,)
        )
        conn.commit()


def deny_access_request(email: str) -> None:
    """Mark an access request as denied."""
    email = email.lower()
    with get_db() as conn:
        conn.execute(
            "UPDATE access_requests SET status = 'denied' WHERE email = ?", (email,)
        )
        conn.commit()


def add_email_to_whitelist(email: str) -> None:
    """Add an email directly to the allowed_emails whitelist."""
    email = email.lower()
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO allowed_emails (email) VALUES (?)", (email,))
        conn.commit()
