"""API-Football client for fetching match data."""
import asyncio
import httpx
import os
from typing import List, Dict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

LEAGUE_IDS = {
    "Premier League": 39,
    "La Liga": 140,
    "Serie A": 135,
    "Bundesliga": 78,
    "Ligue 1": 61,
    "Liga Portugal": 94,
    "Champions League": 2,
}

LEAGUE_NAMES = {v: k for k, v in LEAGUE_IDS.items()}
TARGET_LEAGUE_IDS = set(LEAGUE_IDS.values())

_api_request_count = 0
_fixture_cache: Dict[str, List[Dict]] = {}

# Limit concurrent API requests to avoid rate-limiting
_semaphore = asyncio.Semaphore(2)


def get_api_key() -> str:
    api_key = os.getenv("API_FOOTBALL_KEY")
    if not api_key:
        raise ValueError("API_FOOTBALL_KEY not found in environment")
    return api_key


def get_request_count() -> int:
    return _api_request_count


def clear_cache():
    global _fixture_cache
    _fixture_cache.clear()
    logger.info("Fixture cache cleared")


async def _fetch_fixtures_by_date(
    client: httpx.AsyncClient, date: str, headers: dict
) -> List[Dict]:
    """Fetch all fixtures for a single date. One API call, rate-limited by semaphore."""
    global _api_request_count

    if date in _fixture_cache:
        return _fixture_cache[date]

    async with _semaphore:
        # Check again after acquiring semaphore (another coroutine may have cached it)
        if date in _fixture_cache:
            return _fixture_cache[date]

        url = f"{API_FOOTBALL_BASE}/fixtures"
        params = {"date": date}

        try:
            _api_request_count += 1
            req_num = _api_request_count
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            fixtures = data.get("response", [])

            # Retry once if we got 0 results (possible rate-limit glitch)
            if not fixtures:
                await asyncio.sleep(0.5)
                _api_request_count += 1
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                fixtures = data.get("response", [])
                if fixtures:
                    logger.info(f"[API #{req_num}] {date}: retry succeeded, {len(fixtures)} fixtures")

            _fixture_cache[date] = fixtures

            target_count = sum(1 for f in fixtures if f["league"]["id"] in TARGET_LEAGUE_IDS)
            logger.info(f"[API #{req_num}] {date}: {len(fixtures)} total, {target_count} target leagues")
            return fixtures
        except Exception as e:
            logger.error(f"[API] Error fetching {date}: {e}")
            return []  # Don't cache errors — allow retry


def _filter(fixtures: List[Dict], status: str) -> List[Dict]:
    return [
        f for f in fixtures
        if f["league"]["id"] in TARGET_LEAGUE_IDS
        and f["fixture"]["status"]["short"] == status
    ]


async def _fetch_date_range(dates: List[str], status: str) -> List[Dict]:
    """Fetch fixtures for a list of dates, filter by status, return combined results."""
    api_key = get_api_key()
    headers = {"x-apisports-key": api_key}

    before = _api_request_count
    results: list[Dict] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        tasks = [_fetch_fixtures_by_date(client, d, headers) for d in dates]
        all_days = await asyncio.gather(*tasks)
        for day_fixtures in all_days:
            results.extend(_filter(day_fixtures, status))

    calls = _api_request_count - before
    logger.info(
        f"Fetched {len(results)} {status} fixtures across {len(dates)} days "
        f"({calls} new API calls, {_api_request_count} total session)"
    )
    return results


async def fetch_upcoming_fixtures(days_ahead: int = 14) -> List[Dict]:
    """Fetch upcoming (NS) fixtures for the next N days."""
    today = datetime.now()
    dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_ahead + 1)]
    return await _fetch_date_range(dates, "NS")


async def fetch_recent_results(days_back: int = 14) -> List[Dict]:
    """Fetch finished (FT) fixtures for the past N days."""
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_back + 1)]
    return await _fetch_date_range(dates, "FT")
