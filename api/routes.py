"""FastAPI route handlers for GoalCast."""
from fastapi import APIRouter
from typing import List, Dict, Optional
from datetime import datetime
import logging

from models import Match, Prediction, MatchWithPrediction
from api.club_elo import fetch_elo_ratings
from api.football_api import fetch_upcoming_fixtures, fetch_recent_results, LEAGUE_NAMES, get_request_count, clear_cache
from prediction.engine import generate_prediction
from db import get_db, upsert_match, upsert_ai_prediction, get_all_matches_from_db, get_match_count, get_last_refresh, set_last_refresh, export_db_to_dict, save_seed_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_elo_cache: Dict[str, float] = {}

# API-Football name -> ClubElo name
_TEAM_ALIASES = {
    "Paris Saint Germain": "Paris SG",
    "Atletico Madrid": "Atletico",
    "Bayern Munich": "Bayern", "Bayern München": "Bayern",
    "Borussia Dortmund": "Dortmund",
    "Sporting CP": "Sporting",
    "AC Milan": "Milan",
    "AS Roma": "Roma",
    "Bayer Leverkusen": "Leverkusen",
    "RB Leipzig": "Leipzig",
    "VfB Stuttgart": "Stuttgart",
    "SC Freiburg": "Freiburg",
    "FSV Mainz 05": "Mainz",
    "1899 Hoffenheim": "Hoffenheim",
    "1. FC Heidenheim": "Heidenheim",
    "Werder Bremen": "Bremen",
    "Union Berlin": "Union",
    "Eintracht Frankfurt": "Frankfurt",
    "Borussia Monchengladbach": "Gladbach",
    "FC Porto": "Porto",
    "Real Sociedad": "Sociedad",
    "Real Betis": "Betis",
    "Rayo Vallecano": "Vallecano",
    "Celta Vigo": "Celta",
    "Hellas Verona": "Verona",
    "Nottingham Forest": "Nott'm Forest",
    "Crystal Palace": "Crystal P",
    "Manchester City": "Man City",
    "Manchester United": "Man United",
    "Wolverhampton Wanderers": "Wolves",
    "Bodo/Glimt": "Bodoe Glimt",
    "Borussia M'gladbach": "Gladbach",
    "Saint-Etienne": "St Etienne",
    "Stade Rennais FC": "Rennes",
    "LOSC Lille": "Lille",
    "RC Strasbourg Alsace": "Strasbourg",
    "FC Nantes": "Nantes",
    "OGC Nice": "Nice",
    "AS Monaco": "Monaco",
    "Olympique Lyonnais": "Lyon",
    "Olympique De Marseille": "Marseille",
    "Le Havre AC": "Le Havre",
    "FC Metz": "Metz",
    "Toulouse FC": "Toulouse",
    "Stade De Reims": "Reims",
    "RC Lens": "Lens",
    "Paris FC": "Paris FC",
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
        shorter = min(team_lower, club_lower, key=len)
        longer = max(team_lower, club_lower, key=len)
        if len(shorter) >= 4 and shorter in longer and len(shorter) / len(longer) > 0.5:
            return elo

    return None


def _parse_fixture(fixture: dict) -> dict:
    league_id = fixture["league"]["id"]
    return {
        "home_team": fixture["teams"]["home"]["name"],
        "away_team": fixture["teams"]["away"]["name"],
        "match_date": datetime.fromisoformat(
            fixture["fixture"]["date"].replace("Z", "+00:00")
        ),
        "api_match_id": str(fixture["fixture"]["id"]),
        "league": LEAGUE_NAMES.get(league_id, fixture["league"]["name"]),
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


# ─── Endpoints ────────────────────────────────────────────────


@router.get("/matches")
async def get_matches(status: Optional[str] = None, days_back: Optional[int] = None) -> list:
    """Serve all matches from the database. Fast, no API calls.
    Optional filters: ?status=upcoming or ?status=finished or ?days_back=30
    """
    rows = get_all_matches_from_db()
    results = []
    for row in rows:
        item = _db_row_to_response(row)
        if item:
            if status and item["match"]["status"] != status:
                continue
            results.append(item)
    logger.info(f"Serving {len(results)} matches from DB")
    return results


async def do_refresh(source: str = "manual") -> dict:
    """Core refresh logic — reusable by endpoint, background task, and auto-refresh.

    Startup refreshes go back 30 days for maximum history recovery.
    Manual/scheduled refreshes go back 14 days to conserve API calls.
    """
    logger.info(f"=== REFRESH ({source}): upcoming=14d, recent=14d ===")

    # Clear stale fixture cache so we get fresh API data
    clear_cache()

    # Clear Elo cache too so we get today's ratings
    global _elo_cache
    _elo_cache = {}

    elo_ratings = await _ensure_elo_cache()
    before = get_request_count()

    upcoming_fixtures = await fetch_upcoming_fixtures(days_ahead=14)
    recent_fixtures = await fetch_recent_results(days_back=14)

    after = get_request_count()
    api_calls = after - before
    logger.info(f"API calls this refresh: {api_calls} (session total: {after})")

    # Log what we got per league
    league_counts = {}
    for f in upcoming_fixtures + recent_fixtures:
        lg = LEAGUE_NAMES.get(f["league"]["id"], "?")
        league_counts[lg] = league_counts.get(lg, 0) + 1
    for lg_name in LEAGUE_NAMES.values():
        count = league_counts.get(lg_name, 0)
        if count == 0:
            logger.warning(f"No fixtures found for {lg_name} — API may have limited data for this period")
        else:
            logger.info(f"  {lg_name}: {count} fixtures fetched")

    added = 0
    updated = 0
    skipped_no_elo = 0

    all_fixtures = [
        (f, "upcoming") for f in upcoming_fixtures
    ] + [
        (f, "finished") for f in recent_fixtures
    ]

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
                parsed["home_team"], parsed["away_team"], home_elo, away_elo
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

    logger.info(
        f"Refresh ({source}) done: +{added} new, {updated} updated, "
        f"{skipped_no_elo} skipped (no Elo), {total_in_db} total in DB, {api_calls} API calls"
    )

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


@router.post("/refresh")
async def refresh_data() -> dict:
    """Manual refresh triggered by Refresh button."""
    return await do_refresh(source="manual")


@router.post("/refresh-if-stale")
async def refresh_if_stale() -> dict:
    """Auto-refresh on page load if last refresh was more than 2 hours ago."""
    last = get_last_refresh()
    if last:
        from datetime import datetime as dt
        try:
            last_dt = dt.fromisoformat(last)
            age_hours = (dt.utcnow() - last_dt).total_seconds() / 3600
            if age_hours < 2:
                return {"skipped": True, "age_hours": round(age_hours, 1), "total_in_db": get_match_count()}
        except Exception:
            pass
    return await do_refresh(source="auto-stale")


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
async def save_prediction(data: dict) -> dict:
    match_id = data.get("match_id")
    home = data.get("home")
    away = data.get("away")
    if match_id is None or home is None or away is None:
        return {"error": "match_id, home, away required"}
    logger.info(f"User prediction saved: match {match_id} -> {home}-{away}")
    return {"status": "saved", "match_id": match_id, "home": home, "away": away}


@router.delete("/predictions/{match_id}")
async def delete_prediction(match_id: str) -> dict:
    logger.info(f"User prediction deleted: match {match_id}")
    return {"status": "deleted", "match_id": match_id}


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
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "matches_in_db": get_match_count(),
        "last_refresh": get_last_refresh(),
        "api_key_set": bool(os.environ.get("API_FOOTBALL_KEY")),
    }
