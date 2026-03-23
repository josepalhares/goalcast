"""Football-data.org v4 API client for fetching match data."""
import asyncio
import httpx
import os
from typing import List, Dict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

API_BASE = "https://api.football-data.org/v4"

# Competition codes → display names (free tier of football-data.org)
COMPETITIONS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "PPL": "Liga Portugal",
    "CL": "Champions League",
    # EL (Europa League) not available on free tier
}

LEAGUE_NAMES = dict(COMPETITIONS)

_api_request_count = 0


def get_api_key() -> str:
    api_key = os.getenv("FOOTBALL_DATA_KEY")
    if not api_key:
        raise ValueError("FOOTBALL_DATA_KEY not found in environment")
    return api_key


def get_request_count() -> int:
    return _api_request_count


def clear_cache():
    pass


def _normalize_match(match: dict) -> Dict:
    """Convert football-data.org match format to our internal format."""
    comp_code = match["competition"]["code"]
    ft = match.get("score", {}).get("fullTime", {})
    status_map = {"FINISHED": "FT", "SCHEDULED": "NS", "TIMED": "NS", "IN_PLAY": "LIVE"}
    api_status = status_map.get(match["status"], match["status"])

    return {
        "fixture": {
            "id": match["id"],
            "date": match["utcDate"],
            "status": {"short": api_status},
        },
        "league": {
            "id": comp_code,
            "name": COMPETITIONS.get(comp_code, match["competition"]["name"]),
        },
        "teams": {
            "home": {"name": match["homeTeam"]["name"]},
            "away": {"name": match["awayTeam"]["name"]},
        },
        "goals": {
            "home": ft.get("home"),
            "away": ft.get("away"),
        },
    }


async def _fetch_competition_matches(
    client: httpx.AsyncClient, headers: dict, comp_code: str,
    date_from: str, date_to: str,
) -> List[Dict]:
    """Fetch matches for a single competition in a date range."""
    global _api_request_count

    url = f"{API_BASE}/competitions/{comp_code}/matches"
    params = {"dateFrom": date_from, "dateTo": date_to}

    try:
        _api_request_count += 1
        req_num = _api_request_count

        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        matches = data.get("matches", [])

        logger.info(f"[API #{req_num}] {comp_code} {date_from}→{date_to}: {len(matches)} matches")
        return matches

    except httpx.HTTPStatusError as e:
        logger.error(f"[API] {comp_code} HTTP {e.response.status_code}: {e.response.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"[API] {comp_code} error: {e}")
        return []


async def _fetch_all_competitions(date_from: str, date_to: str) -> List[Dict]:
    """Fetch matches for ALL competitions in a date range.
    One API call per competition — gives complete data unlike general /matches endpoint.
    Rate limit: 10 req/min on free tier, we do 7 sequentially with small delays.
    """
    api_key = get_api_key()
    headers = {"X-Auth-Token": api_key}
    all_matches = []
    seen_ids = set()

    async with httpx.AsyncClient(timeout=20.0) as client:
        for comp_code in COMPETITIONS:
            matches = await _fetch_competition_matches(
                client, headers, comp_code, date_from, date_to
            )
            for m in matches:
                if m["id"] not in seen_ids:
                    seen_ids.add(m["id"])
                    all_matches.append(m)
            # Free tier: 10 requests/minute. Space calls ~7s apart to stay safe.
            await asyncio.sleep(7)

    logger.info(f"All competitions {date_from}→{date_to}: {len(all_matches)} total matches")
    return all_matches


async def _fetch_range_chunked(date_from: str, date_to: str) -> List[Dict]:
    """Fetch matches in 10-day chunks across all competitions."""
    from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    to_dt = datetime.strptime(date_to, "%Y-%m-%d")
    all_matches = []
    seen_ids = set()

    while from_dt <= to_dt:
        chunk_end = min(from_dt + timedelta(days=9), to_dt)
        chunk = await _fetch_all_competitions(
            from_dt.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d"),
        )
        for m in chunk:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                all_matches.append(m)
        from_dt = chunk_end + timedelta(days=1)

    return all_matches


async def fetch_upcoming_fixtures(days_ahead: int = 14) -> List[Dict]:
    """Fetch upcoming fixtures (TIMED or SCHEDULED). Returns normalized format."""
    today = datetime.now()
    date_from = today.strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    all_matches = await _fetch_range_chunked(date_from, date_to)
    upcoming = [m for m in all_matches if m["status"] in ("TIMED", "SCHEDULED")]

    normalized = [_normalize_match(m) for m in upcoming]
    logger.info(f"Upcoming: {len(normalized)} fixtures ({date_from} to {date_to})")
    return normalized


async def fetch_recent_results(days_back: int = 14) -> List[Dict]:
    """Fetch finished results. Returns normalized format."""
    today = datetime.now()
    date_from = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
    date_to = today.strftime("%Y-%m-%d")

    all_matches = await _fetch_range_chunked(date_from, date_to)
    finished = [m for m in all_matches if m["status"] == "FINISHED"]

    normalized = [_normalize_match(m) for m in finished]
    logger.info(f"Recent: {len(normalized)} results ({date_from} to {date_to})")
    return normalized
