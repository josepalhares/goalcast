"""FastAPI route handlers for GoalCast."""
from fastapi import APIRouter, HTTPException, Request
from typing import List, Dict, Optional
from datetime import datetime
import logging

from models import Match, Prediction, MatchWithPrediction
from api.club_elo import fetch_elo_ratings
from api.football_api import fetch_matches as fetch_fd_matches, LEAGUE_NAMES, get_request_count, clear_cache
from api.espn_api import fetch_espn_matches
from api.national_elo import get_national_elo
from prediction.engine import generate_prediction, set_calibration as set_engine_calibration, fit_model, set_dc_model, log_accuracy_report
from db import get_db, upsert_match, upsert_ai_prediction, get_all_matches_from_db, get_match_count, get_last_refresh, set_last_refresh, export_db_to_dict, save_seed_file, calculate_calibration, get_calibration, save_user_prediction, delete_user_prediction, get_user_predictions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_elo_cache: Dict[str, float] = {}

# Team name -> ClubElo name (covers both API-Football and football-data.org names)
_TEAM_ALIASES = {
    # football-data.org names (with FC/CF suffixes)
    "Arsenal FC": "Arsenal",
    "Chelsea FC": "Chelsea",
    "Liverpool FC": "Liverpool",
    "Manchester City FC": "Man City",
    "Manchester United FC": "Man United",
    "Tottenham Hotspur FC": "Tottenham",
    "Newcastle United FC": "Newcastle",
    "FC Barcelona": "Barcelona",
    "Real Madrid CF": "Real Madrid",
    "Club Atlético de Madrid": "Atletico",
    "FC Bayern München": "Bayern",
    "Bayer 04 Leverkusen": "Leverkusen",
    "Paris Saint-Germain FC": "Paris SG",
    "Sporting Clube de Portugal": "Sporting",
    "FK Bodø/Glimt": "Bodoe Glimt",
    "Galatasaray SK": "Galatasaray",
    "Atalanta BC": "Atalanta",
    "AC Milan": "Milan",
    "AS Roma FC": "Roma", "AS Roma": "Roma",
    "SSC Napoli": "Napoli",
    "SS Lazio": "Lazio",
    "ACF Fiorentina": "Fiorentina",
    "Bologna FC 1909": "Bologna",
    "FC Internazionale Milano": "Inter",
    "Juventus FC": "Juventus",
    "Genoa CFC": "Genoa",
    "Udinese Calcio": "Udinese",
    "Cagliari Calcio": "Cagliari",
    "US Lecce": "Lecce",
    "Hellas Verona FC": "Verona",
    "Borussia Dortmund": "Dortmund",
    "RB Leipzig": "Leipzig",
    "VfB Stuttgart": "Stuttgart",
    "SC Freiburg": "Freiburg",
    "TSG 1899 Hoffenheim": "Hoffenheim",
    "1. FC Heidenheim 1846": "Heidenheim",
    "SV Werder Bremen": "Bremen",
    "1. FC Union Berlin": "Union",
    "Eintracht Frankfurt": "Frankfurt",
    "Borussia Mönchengladbach": "Gladbach",
    "VfL Wolfsburg": "Wolfsburg",
    "FC Augsburg": "Augsburg",
    "1. FSV Mainz 05": "Mainz",
    "FC Porto": "Porto",
    "SL Benfica": "Benfica",
    "Sport Lisboa e Benfica": "Benfica",
    "Sporting CP": "Sporting",
    "SC Braga": "Braga",
    "Sporting Clube de Braga": "Braga",
    "Vitória SC": "Guimaraes",
    "Rio Ave FC": "Rio Ave",
    "CD Santa Clara": "Santa Clara",
    "CD Tondela": "Tondela",
    "Gil Vicente FC": "Gil Vicente",
    "Moreirense FC": "Moreirense",
    "GD Estoril Praia": "Estoril",
    "CF Estrela da Amadora": "Estrela Amadora",
    "CD Nacional": "Nacional",
    "FC Famalicão": "Famalicao",
    "FC Arouca": "Arouca",
    "Casa Pia AC": "Casa Pia",
    "FC Alverca": "Alverca",
    "Real Sociedad de Fútbol": "Sociedad",
    "Real Betis Balompié": "Betis",
    "Villarreal CF": "Villarreal",
    "RC Celta de Vigo": "Celta",
    "Sevilla FC": "Sevilla",
    "Valencia CF": "Valencia",
    "RCD Mallorca": "Mallorca",
    "Rayo Vallecano de Madrid": "Vallecano",
    "Girona FC": "Girona",
    "RCD Espanyol de Barcelona": "Espanyol",
    "CA Osasuna": "Osasuna",
    "Olympique de Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon",
    "LOSC Lille": "Lille",
    "AS Monaco FC": "Monaco",
    "OGC Nice": "Nice",
    "Stade Rennais FC 1901": "Rennes",
    "RC Strasbourg Alsace": "Strasbourg",
    "RC Lens": "Lens",
    "Stade de Reims": "Reims",
    "Toulouse FC": "Toulouse",
    "FC Nantes": "Nantes",
    "Le Havre AC": "Le Havre",
    "Montpellier HSC": "Montpellier",
    "Angers SCO": "Angers",
    "AJ Auxerre": "Auxerre",
    "AS Saint-Étienne": "St Etienne",
    "Wolverhampton Wanderers FC": "Wolves",
    "Aston Villa FC": "Aston Villa",
    "West Ham United FC": "West Ham",
    "Brighton & Hove Albion FC": "Brighton",
    "Crystal Palace FC": "Crystal P",
    "AFC Bournemouth": "Bournemouth",
    "Brentford FC": "Brentford",
    "Nottingham Forest FC": "Nott'm Forest",
    "Fulham FC": "Fulham",
    "Everton FC": "Everton",
    "Leicester City FC": "Leicester",
    "Ipswich Town FC": "Ipswich",
    "Southampton FC": "Southampton",
    "Leeds United FC": "Leeds",
    # Legacy API-Football names (keep for seed compatibility)
    "Paris Saint Germain": "Paris SG",
    "Atletico Madrid": "Atletico",
    "Bayern Munich": "Bayern", "Bayern München": "Bayern",
    "Bodo/Glimt": "Bodoe Glimt",
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "Nottingham Forest": "Nott'm Forest",
    "Crystal Palace": "Crystal P",
    "Bayer Leverkusen": "Leverkusen",
}


async def _ensure_elo_cache() -> Dict[str, float]:
    global _elo_cache
    if not _elo_cache:
        _elo_cache = await fetch_elo_ratings()
    return _elo_cache


def _find_elo(team_name: str, elo_ratings: Dict[str, float]) -> Optional[float]:
    if team_name in elo_ratings:
        return elo_ratings[team_name]

    alias = _TEAM_ALIASES.get(team_name)
    if alias and alias in elo_ratings:
        return elo_ratings[alias]

    team_lower = team_name.lower()
    for club, elo in elo_ratings.items():
        if club.lower() == team_lower:
            return elo

    for club, elo in elo_ratings.items():
        club_lower = club.lower()
        # Ensure shorter/longer are correctly assigned (not same string)
        if len(team_lower) <= len(club_lower):
            shorter, longer = team_lower, club_lower
        else:
            shorter, longer = club_lower, team_lower
        if len(shorter) >= 4 and shorter != longer and shorter in longer and len(shorter) / len(longer) > 0.5:
            return elo

    # National team fallback (ClubElo only has clubs, not national teams)
    national = get_national_elo(team_name)
    if national is not None:
        return national

    return None


def _parse_fixture(fixture: dict) -> dict:
    date_str = fixture["fixture"]["date"]
    # Handle both 'Z' suffix and '+00:00' formats
    if date_str.endswith("Z"):
        date_str = date_str.replace("Z", "+00:00")
    return {
        "home_team": fixture["teams"]["home"]["name"],
        "away_team": fixture["teams"]["away"]["name"],
        "match_date": datetime.fromisoformat(date_str),
        "api_match_id": str(fixture["fixture"]["id"]),
        "league": fixture["league"]["name"],
        "home_goals": fixture["goals"]["home"],
        "away_goals": fixture["goals"]["away"],
    }


def _db_row_to_response(row: dict) -> Optional[dict]:
    """Convert a DB row to the API response format."""
    if row.get("predicted_home_goals") is None:
        return None  # No AI prediction yet

    match = {
        "id": row["id"],
        "api_match_id": row["api_match_id"],
        "league": row["league"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "match_date": row["match_date"],
        "home_elo": row["home_elo"],
        "away_elo": row["away_elo"],
        "status": row["status"],
        "actual_home_goals": row["actual_home_goals"],
        "actual_away_goals": row["actual_away_goals"],
    }
    ph = row["predicted_home_goals"]
    pa = row["predicted_away_goals"]
    hw = row["home_win_prob"] or 0
    dw = row["draw_prob"] or 0
    aw = row["away_win_prob"] or 0

    if ph > pa:
        conf_pct = round(hw * 100)
    elif ph < pa:
        conf_pct = round(aw * 100)
    else:
        conf_pct = round(dw * 100)

    prediction = {
        "match_id": row["id"],
        "source": "ai",
        "predicted_home_goals": ph,
        "predicted_away_goals": pa,
        "home_win_prob": hw,
        "draw_prob": dw,
        "away_win_prob": aw,
        "confidence": row.get("confidence", "medium"),
        "confidence_pct": conf_pct,
    }
    return {"match": match, "prediction": prediction}


# ─── Response cache ────────────────────────────────────────────

import time as _time

_matches_cache: Optional[list] = None
_matches_cache_time: float = 0
_CACHE_TTL = 60  # seconds


def _invalidate_matches_cache():
    global _matches_cache, _matches_cache_time
    _matches_cache = None
    _matches_cache_time = 0


# ─── Endpoints ────────────────────────────────────────────────


@router.get("/matches")
async def get_matches(request: Request, status: Optional[str] = None) -> list:
    """Serve all matches from DB with user predictions if logged in."""
    global _matches_cache, _matches_cache_time

    now = _time.time()
    if _matches_cache is None or (now - _matches_cache_time) > _CACHE_TTL:
        rows = get_all_matches_from_db()
        _matches_cache = [_db_row_to_response(row) for row in rows]
        _matches_cache = [m for m in _matches_cache if m is not None]
        _matches_cache_time = now

    result = _matches_cache
    if status:
        result = [m for m in result if m["match"]["status"] == status]

    # Attach user predictions if logged in
    user = request.session.get("user")
    if user:
        from db import get_user_predictions as _gup
        user_row = None
        with get_db() as conn:
            user_row = conn.execute("SELECT id FROM users WHERE email = ?", (user["email"],)).fetchone()
        if user_row:
            up = _gup(user_row["id"])
            # Attach to each match
            result = [dict(m) for m in result]  # shallow copy
            for m in result:
                mid = m["match"]["api_match_id"]
                if mid in up:
                    m["user_prediction"] = up[mid]

    return result


async def do_refresh(source: str = "manual") -> dict:
    """Core refresh logic — reusable by endpoint, background task, and auto-refresh.

    Optimized: single-pass football-data.org (no date chunking), parallel ESPN,
    concurrent fetching, conditional Dixon-Coles refit.
    """
    import asyncio as _aio
    logger.info(f"=== REFRESH ({source}) ===")

    # Clear stale caches
    clear_cache()
    global _elo_cache
    _elo_cache = {}

    # Fetch Elo ratings (needed before processing fixtures)
    elo_ratings = await _ensure_elo_cache()

    before = get_request_count()
    db_has_data = get_match_count() > 0

    # Run football-data.org (single pass, rate-limited) and ESPN (all parallel) concurrently
    (upcoming_fixtures, recent_fixtures), espn_matches = await _aio.gather(
        fetch_fd_matches(days_back=14, days_ahead=14),
        fetch_espn_matches(db_has_data=db_has_data),
    )

    after = get_request_count()
    api_calls = after - before
    logger.info(f"API calls: {api_calls} football-data.org + {len(espn_matches)} ESPN matches")

    # Log per-league counts
    league_counts: dict = {}
    for f in upcoming_fixtures + recent_fixtures + espn_matches:
        lg = f["league"]["name"]
        league_counts[lg] = league_counts.get(lg, 0) + 1
    from api.espn_api import COMPETITIONS as ESPN_COMPS
    for lg_name in list(LEAGUE_NAMES.values()) + list(ESPN_COMPS.values()):
        count = league_counts.get(lg_name, 0)
        if count == 0:
            logger.warning(f"No fixtures for {lg_name}")
        else:
            logger.info(f"  {lg_name}: {count}")

    added = 0
    updated = 0
    skipped_no_elo = 0

    all_fixtures = [
        (f, "upcoming") for f in upcoming_fixtures
    ] + [
        (f, "finished") for f in recent_fixtures
    ]
    for f in espn_matches:
        s = f["fixture"]["status"]["short"]
        if s == "FT":
            all_fixtures.append((f, "finished"))
        elif s == "NS":
            all_fixtures.append((f, "upcoming"))

    for fixture, status in all_fixtures:
        try:
            parsed = _parse_fixture(fixture)
            home_elo = _find_elo(parsed["home_team"], elo_ratings)
            away_elo = _find_elo(parsed["away_team"], elo_ratings)

            if home_elo is None or away_elo is None:
                skipped_no_elo += 1
                continue

            existing = None
            with get_db() as conn:
                existing = conn.execute(
                    "SELECT id, status FROM matches WHERE api_match_id = ?",
                    (parsed["api_match_id"],)
                ).fetchone()

            actual_h = parsed["home_goals"] if status == "finished" else None
            actual_a = parsed["away_goals"] if status == "finished" else None

            match_id = upsert_match(
                api_match_id=parsed["api_match_id"],
                league=parsed["league"],
                home_team=parsed["home_team"],
                away_team=parsed["away_team"],
                match_date=parsed["match_date"].isoformat(),
                home_elo=home_elo,
                away_elo=away_elo,
                status=status,
                actual_home_goals=actual_h,
                actual_away_goals=actual_a,
            )

            if existing:
                if existing["status"] != status:
                    updated += 1
            else:
                added += 1

            pred = generate_prediction(
                parsed["home_team"], parsed["away_team"], home_elo, away_elo,
                league=parsed["league"]
            )
            upsert_ai_prediction(
                match_id=match_id,
                predicted_home_goals=pred["predicted_home_goals"],
                predicted_away_goals=pred["predicted_away_goals"],
                home_win_prob=pred["home_win_prob"],
                draw_prob=pred["draw_prob"],
                away_win_prob=pred["away_win_prob"],
                confidence=pred["confidence"],
            )

        except Exception as e:
            logger.error(f"Error processing fixture: {e}")

    total_in_db = get_match_count()
    set_last_refresh(datetime.utcnow().isoformat())

    # Log DB totals per league
    with get_db() as conn:
        rows = conn.execute(
            "SELECT league, COUNT(*) as cnt, "
            "SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END) as finished "
            "FROM matches GROUP BY league ORDER BY league"
        ).fetchall()
        logger.info(f"DB totals after refresh ({total_in_db} matches):")
        for r in rows:
            logger.info(f"  {r['league']}: {r['cnt']} total ({r['finished']} finished)")

    # Recalculate model calibration from all finished matches
    cal = calculate_calibration()
    if cal:
        set_engine_calibration(cal)

    # Only re-fit Dixon-Coles if new finished results were added
    if updated > 0 or (added > 0 and any(s == "finished" for _, s in all_fixtures)):
        try:
            import asyncio as _aio2
            with get_db() as conn:
                # Cap to most recent 200 finished matches to keep fitting fast
                finished_rows = conn.execute("""
                    SELECT home_team, away_team, actual_home_goals, actual_away_goals, match_date, league
                    FROM matches WHERE status = 'finished' AND actual_home_goals IS NOT NULL
                    ORDER BY match_date DESC LIMIT 200
                """).fetchall()
            dc_data = [dict(r) for r in finished_rows]
            dc_model = await _aio2.get_event_loop().run_in_executor(None, fit_model, dc_data)
            set_dc_model(dc_model)
            logger.info(f"Dixon-Coles re-fitted ({len(dc_data)} matches)")
        except Exception as e:
            logger.error(f"Dixon-Coles fitting failed (non-fatal): {e}")

        # Log accuracy report
        try:
            with get_db() as conn:
                report_rows = conn.execute("""
                    SELECT m.home_team, m.away_team, m.actual_home_goals, m.actual_away_goals,
                           m.league, p.predicted_home_goals as pred_h, p.predicted_away_goals as pred_a
                    FROM matches m JOIN predictions p ON p.match_id = m.id AND p.source = 'ai'
                    WHERE m.status = 'finished' AND m.actual_home_goals IS NOT NULL
                """).fetchall()
            log_accuracy_report([dict(r) for r in report_rows])
        except Exception as e:
            logger.error(f"Accuracy report failed (non-fatal): {e}")
    else:
        logger.info("Skipping Dixon-Coles refit (no new finished matches)")

    logger.info(
        f"Refresh ({source}) done: +{added} new, {updated} updated, "
        f"{skipped_no_elo} skipped (no Elo), {total_in_db} total in DB, {api_calls} API calls"
    )

    # Invalidate response cache so next request gets fresh data
    _invalidate_matches_cache()

    # Auto-export seed after every refresh
    try:
        save_seed_file()
    except Exception as e:
        logger.error(f"Failed to auto-export seed: {e}")

    return {
        "added": added,
        "updated": updated,
        "total_in_db": total_in_db,
        "api_calls_used": api_calls,
    }


_is_refreshing = False
_refresh_started_at: float = 0
_REFRESH_TIMEOUT = 300  # 5 minutes — auto-reset if stuck


def _start_bg_refresh(source: str = "manual"):
    """Fire a refresh as a background asyncio task. Returns immediately."""
    import asyncio as _aio
    global _is_refreshing, _refresh_started_at

    # Auto-reset if stuck for > 5 minutes
    if _is_refreshing and (_time.time() - _refresh_started_at) > _REFRESH_TIMEOUT:
        logger.warning("Refresh was stuck — auto-resetting _is_refreshing")
        _is_refreshing = False

    if _is_refreshing:
        return False

    _is_refreshing = True
    _refresh_started_at = _time.time()

    async def _bg():
        global _is_refreshing
        try:
            result = await do_refresh(source=source)
            logger.info(f"Background refresh ({source}) done: {result}")
        except Exception as e:
            logger.error(f"Background refresh ({source}) failed: {e}")
        finally:
            _is_refreshing = False

    _aio.create_task(_bg())
    return True


@router.post("/refresh")
async def refresh_data() -> dict:
    """Trigger refresh in background — returns immediately."""
    started = _start_bg_refresh(source="manual")
    if not started:
        return {"status": "already_running"}
    return {"status": "started"}


@router.get("/refresh-status")
async def refresh_status() -> dict:
    """Poll this to check if a background refresh is done."""
    return {
        "is_refreshing": _is_refreshing,
        "last_refresh": get_last_refresh(),
        "matches_in_db": get_match_count(),
    }


@router.get("/cron-refresh")
async def cron_refresh() -> dict:
    """External cron endpoint — triggers refresh if last was >3 hours ago."""
    last = get_last_refresh()
    if last:
        from datetime import datetime as dt
        try:
            last_dt = dt.fromisoformat(last)
            age_hours = (dt.utcnow() - last_dt).total_seconds() / 3600
            if age_hours < 3:
                return {"skipped": True, "age_hours": round(age_hours, 1)}
        except Exception:
            pass

    started = _start_bg_refresh(source="cron")
    return {"triggered": started, "message": "Refresh started in background" if started else "Already running"}


@router.get("/export")
async def export_data() -> dict:
    """Export all matches + predictions as JSON (for seed file backup)."""
    return export_db_to_dict()


@router.post("/export")
async def save_export() -> dict:
    """Save current DB state to data/seed.json."""
    path = save_seed_file()
    count = get_match_count()
    return {"saved": path, "matches": count}


@router.post("/predictions")
async def save_prediction(request: Request, data: dict) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    match_id = data.get("match_id")
    home = data.get("home")
    away = data.get("away")
    if match_id is None or home is None or away is None:
        return {"error": "match_id, home, away required"}

    with get_db() as conn:
        user_row = conn.execute("SELECT id FROM users WHERE email = ?", (user["email"],)).fetchone()
    if user_row:
        save_user_prediction(match_id, user_row["id"], int(home), int(away))
        logger.info(f"User prediction saved (DB): {user['email']} match {match_id} -> {home}-{away}")
        return {"status": "saved", "match_id": match_id, "home": home, "away": away, "stored": "server"}
    return {"error": "User not found"}


@router.delete("/predictions/{match_id}")
async def delete_pred(request: Request, match_id: str) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    with get_db() as conn:
        user_row = conn.execute("SELECT id FROM users WHERE email = ?", (user["email"],)).fetchone()
    if user_row:
        delete_user_prediction(match_id, user_row["id"])
        logger.info(f"User prediction deleted (DB): {user['email']} match {match_id}")
        return {"status": "deleted", "match_id": match_id}
    return {"error": "User not found"}


@router.post("/accuracy")
async def get_accuracy(user_preds: dict = {}) -> dict:
    """Calculate accuracy stats from the database."""
    logger.info("Calculating accuracy stats from DB")

    rows = get_all_matches_from_db()
    finished: list[dict] = []
    for row in rows:
        if row["status"] != "finished" or row["actual_home_goals"] is None:
            continue
        if row["predicted_home_goals"] is None:
            continue
        finished.append({
            "match_id": row["api_match_id"],
            "league": row["league"],
            "date": row["match_date"][:10] if isinstance(row["match_date"], str) else row["match_date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "actual_h": row["actual_home_goals"],
            "actual_a": row["actual_away_goals"],
            "ai_h": row["predicted_home_goals"],
            "ai_a": row["predicted_away_goals"],
        })

    def _outcome(h, a):
        if h > a: return "W"
        if h < a: return "L"
        return "D"

    total = len(finished)

    ai_exact = ai_outcome = 0
    ai_mae_h = ai_mae_a = 0.0
    for m in finished:
        if m["ai_h"] == m["actual_h"] and m["ai_a"] == m["actual_a"]:
            ai_exact += 1
        if _outcome(m["ai_h"], m["ai_a"]) == _outcome(m["actual_h"], m["actual_a"]):
            ai_outcome += 1
        ai_mae_h += abs(m["ai_h"] - m["actual_h"])
        ai_mae_a += abs(m["ai_a"] - m["actual_a"])

    ai_stats = {
        "exact_score_hits": ai_exact,
        "exact_score_rate": round(ai_exact / total, 3) if total else 0,
        "correct_outcome_hits": ai_outcome,
        "correct_outcome_rate": round(ai_outcome / total, 3) if total else 0,
        "mae_home": round(ai_mae_h / total, 2) if total else 0,
        "mae_away": round(ai_mae_a / total, 2) if total else 0,
        "mae_total": round((ai_mae_h + ai_mae_a) / (2 * total), 2) if total else 0,
    }

    user_exact = user_outcome = 0
    user_mae_h = user_mae_a = 0.0
    user_count = 0
    for m in finished:
        up = user_preds.get(m["match_id"])
        if not up or up.get("home") is None or up.get("away") is None:
            continue
        user_count += 1
        uh, ua = int(up["home"]), int(up["away"])
        if uh == m["actual_h"] and ua == m["actual_a"]:
            user_exact += 1
        if _outcome(uh, ua) == _outcome(m["actual_h"], m["actual_a"]):
            user_outcome += 1
        user_mae_h += abs(uh - m["actual_h"])
        user_mae_a += abs(ua - m["actual_a"])

    user_stats = {
        "predictions_made": user_count,
        "exact_score_hits": user_exact,
        "exact_score_rate": round(user_exact / user_count, 3) if user_count else 0,
        "correct_outcome_hits": user_outcome,
        "correct_outcome_rate": round(user_outcome / user_count, 3) if user_count else 0,
        "mae_home": round(user_mae_h / user_count, 2) if user_count else 0,
        "mae_away": round(user_mae_a / user_count, 2) if user_count else 0,
        "mae_total": round((user_mae_h + user_mae_a) / (2 * user_count), 2) if user_count else 0,
    }

    league_map: Dict[str, dict] = {}
    for m in finished:
        lg = m["league"]
        if lg not in league_map:
            league_map[lg] = {"matches": 0, "ai_outcome": 0, "user_outcome": 0, "user_count": 0}
        league_map[lg]["matches"] += 1
        if _outcome(m["ai_h"], m["ai_a"]) == _outcome(m["actual_h"], m["actual_a"]):
            league_map[lg]["ai_outcome"] += 1
        up = user_preds.get(m["match_id"])
        if up and up.get("home") is not None:
            league_map[lg]["user_count"] += 1
            if _outcome(int(up["home"]), int(up["away"])) == _outcome(m["actual_h"], m["actual_a"]):
                league_map[lg]["user_outcome"] += 1

    by_league = [
        {"league": lg, "matches": d["matches"],
         "ai_outcome_rate": round(d["ai_outcome"] / d["matches"], 3) if d["matches"] else 0,
         "user_outcome_rate": round(d["user_outcome"] / d["user_count"], 3) if d["user_count"] else 0,
         "user_count": d["user_count"]}
        for lg, d in sorted(league_map.items())
    ]

    date_map: Dict[str, dict] = {}
    for m in finished:
        dt = m["date"][:10] if isinstance(m["date"], str) else str(m["date"])[:10]
        if dt not in date_map:
            date_map[dt] = {"total": 0, "ai_exact": 0, "ai_outcome": 0}
        date_map[dt]["total"] += 1
        if m["ai_h"] == m["actual_h"] and m["ai_a"] == m["actual_a"]:
            date_map[dt]["ai_exact"] += 1
        if _outcome(m["ai_h"], m["ai_a"]) == _outcome(m["actual_h"], m["actual_a"]):
            date_map[dt]["ai_outcome"] += 1

    by_date = [{"date": dt, **d} for dt, d in sorted(date_map.items())]

    match_log = []
    for m in sorted(finished, key=lambda x: str(x["date"]), reverse=True):
        up = user_preds.get(m["match_id"])
        user_h = int(up["home"]) if up and up.get("home") is not None else None
        user_a = int(up["away"]) if up and up.get("away") is not None else None
        ai_result = "exact" if (m["ai_h"] == m["actual_h"] and m["ai_a"] == m["actual_a"]) else \
                    "close" if _outcome(m["ai_h"], m["ai_a"]) == _outcome(m["actual_h"], m["actual_a"]) else "miss"
        user_result = None
        if user_h is not None:
            user_result = "exact" if (user_h == m["actual_h"] and user_a == m["actual_a"]) else \
                          "close" if _outcome(user_h, user_a) == _outcome(m["actual_h"], m["actual_a"]) else "miss"
        match_log.append({
            "date": str(m["date"])[:10], "home_team": m["home_team"], "away_team": m["away_team"],
            "league": m["league"], "ai_h": m["ai_h"], "ai_a": m["ai_a"],
            "user_h": user_h, "user_a": user_a,
            "actual_h": m["actual_h"], "actual_a": m["actual_a"],
            "ai_result": ai_result, "user_result": user_result,
        })

    insights = _generate_insights(finished, user_preds, ai_stats, user_stats, league_map)

    return {
        "total_matches": total, "ai": ai_stats, "user": user_stats,
        "by_league": by_league, "by_date": by_date,
        "match_log": match_log, "insights": insights,
    }


def _generate_insights(finished, user_preds, ai_stats, user_stats, league_map):
    insights = []
    total = len(finished)
    if total == 0:
        return ["No finished matches to analyze yet. Hit Refresh to pull match data."]

    def _outcome(h, a):
        return "W" if h > a else ("L" if h < a else "D")

    if user_stats["predictions_made"] >= 3:
        ai_r, user_r = ai_stats["correct_outcome_rate"], user_stats["correct_outcome_rate"]
        if user_r > ai_r:
            insights.append(f"Your predictions outperform the AI ({user_r*100:.0f}% vs {ai_r*100:.0f}% correct outcome).")
        elif ai_r > user_r:
            insights.append(f"The AI edges you on outcomes ({ai_r*100:.0f}% vs {user_r*100:.0f}%). Try adjusting from the AI baseline.")

    ai_draws = sum(1 for m in finished if _outcome(m["ai_h"], m["ai_a"]) == "D")
    actual_draws = sum(1 for m in finished if _outcome(m["actual_h"], m["actual_a"]) == "D")
    if actual_draws > ai_draws and actual_draws >= 3:
        insights.append(f"The AI underestimates draws ({ai_draws} predicted vs {actual_draws} actual). Consider more draws for evenly matched teams.")

    avg_actual = sum(m["actual_h"] + m["actual_a"] for m in finished) / total
    avg_ai = sum(m["ai_h"] + m["ai_a"] for m in finished) / total
    if abs(avg_actual - avg_ai) > 0.3:
        d = "underestimates" if avg_ai < avg_actual else "overestimates"
        insights.append(f"Avg goals/match: {avg_actual:.1f} actual vs {avg_ai:.1f} AI — the model {d} scoring by {abs(avg_actual - avg_ai):.1f}.")

    from collections import Counter
    ai_scores = Counter(f"{m['ai_h']}-{m['ai_a']}" for m in finished)
    top_score, top_count = ai_scores.most_common(1)[0]
    pct = top_count / total * 100
    actual_pct = sum(1 for m in finished if f"{m['actual_h']}-{m['actual_a']}" == top_score) / total * 100
    if pct > 30 and abs(pct - actual_pct) > 10:
        insights.append(f"AI predicted {top_score} for {pct:.0f}% of matches but only {actual_pct:.0f}% ended that way.")

    if len(league_map) >= 3:
        rates = [(lg, d["ai_outcome"]/d["matches"], d["matches"]) for lg, d in league_map.items() if d["matches"] >= 2]
        if rates:
            rates.sort(key=lambda x: x[1])
            if rates[-1][1] > rates[0][1] + 0.15:
                insights.append(f"AI strongest in {rates[-1][0]} ({rates[-1][1]*100:.0f}%), weakest in {rates[0][0]} ({rates[0][1]*100:.0f}%).")

    return insights[:5]


@router.get("/health")
async def health_check() -> dict:
    import os
    cal = get_calibration()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "matches_in_db": get_match_count(),
        "last_refresh": get_last_refresh(),
        "api_key_set": bool(os.environ.get("FOOTBALL_DATA_KEY")),
        "calibration": cal,
    }
