"""Football-data.org v4 API client for fetching match data."""
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

LEAGUE_NAMES = dict(COMPETITIONS)  # Kept for backward compat with routes.py

_api_request_count = 0


def get_api_key() -> str:
    api_key = os.getenv("FOOTBALL_DATA_KEY")
    if not api_key:
        raise ValueError("FOOTBALL_DATA_KEY not found in environment")
    return api_key


def get_request_count() -> int:
    return _api_request_count


def clear_cache():
    """No-op — football-data.org doesn't need client-side caching (single request per fetch)."""
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


async def _fetch_all_matches(date_from: str, date_to: str) -> List[Dict]:
    """Fetch ALL matches (any status) for a date range. One API call."""
    global _api_request_count
    api_key = get_api_key()

    url = f"{API_BASE}/matches"
    headers = {"X-Auth-Token": api_key}
    params = {"dateFrom": date_from, "dateTo": date_to}

    try:
        _api_request_count += 1
        req_num = _api_request_count

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        all_matches = data.get("matches", [])
        comp_codes = set(COMPETITIONS.keys())
        filtered = [m for m in all_matches if m.get("competition", {}).get("code") in comp_codes]

        logger.info(f"[API #{req_num}] {date_from} to {date_to}: {len(all_matches)} total, {len(filtered)} target")
        return filtered

    except httpx.HTTPStatusError as e:
        logger.error(f"[API] HTTP {e.response.status_code}: {e.response.text[:200]}")
        return []
    except Exception as e:
        logger.error(f"[API] Error: {e}")
        return []


async def _fetch_range_chunked(date_from: str, date_to: str) -> List[Dict]:
    """Fetch matches in 10-day chunks (football-data.org free tier limit)."""
    from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    to_dt = datetime.strptime(date_to, "%Y-%m-%d")
    all_matches = []
    seen_ids = set()

    while from_dt <= to_dt:
        chunk_end = min(from_dt + timedelta(days=9), to_dt)  # Max 10 days
        chunk = await _fetch_all_matches(
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
