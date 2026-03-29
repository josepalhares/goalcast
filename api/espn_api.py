"""ESPN API client for Europa League, Conference League, and international football."""
import asyncio
import httpx
from typing import List, Dict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HEADERS = {"User-Agent": "Mozilla/5.0"}

COMPETITIONS = {
    "uefa.europa": "Europa League",
    "uefa.europa.conf": "Conference League",
    # International
    "fifa.worldq.uefa": "WC Qualifiers UEFA",
    "fifa.worldq.conmebol": "WC Qualifiers CONMEBOL",
    "fifa.worldq.concacaf": "WC Qualifiers CONCACAF",
    "uefa.nations": "Nations League",
    "fifa.friendly": "Friendlies",
    "fifa.world": "World Cup",
    "uefa.euro": "European Championship",
}

# ESPN status → our internal status code
STATUS_MAP = {
    "STATUS_FULL_TIME": "FT",
    "STATUS_FINAL_AET": "FT",
    "STATUS_FINAL_PEN": "FT",
    "STATUS_FINAL": "FT",
    "STATUS_SCHEDULED": "NS",
    "STATUS_IN_PROGRESS": "LIVE",
    "STATUS_HALFTIME": "LIVE",
    "STATUS_POSTPONED": "PST",
}

# ESPN short names → football-data.org names (for dedup matching)
TEAM_ALIASES = {
    "Nottm Forest": "Nottingham Forest FC",
    "Midtjylland": "FC Midtjylland",
    "Genk": "Racing Genk",
    "Betis": "Real Betis Balompié",
    "Celta Vigo": "RC Celta de Vigo",
    "Freiburg": "SC Freiburg",
    "Porto": "FC Porto",
    "Stuttgart": "VfB Stuttgart",
    "Aston Villa": "Aston Villa FC",
    "Roma": "AS Roma",
    "Mainz": "1. FSV Mainz 05",
    "Crystal Palace": "Crystal Palace FC",
    "Fiorentina": "ACF Fiorentina",
    "Lyon": "Olympique Lyonnais",
    "Bologna": "Bologna FC 1909",
    "Lille": "LOSC Lille",
    "Braga": "Sporting Clube de Braga",
    "Lazio": "SS Lazio",
    "Athletic Club": "Athletic Club",
    "Fenerbahçe": "Fenerbahce",
    "Galatasaray": "Galatasaray SK",
    "Olympiacos": "Olympiacos Piraeus",
    "Tottenham": "Tottenham Hotspur FC",
    "Manchester United": "Manchester United FC",
    "Rangers": "Rangers FC",
    "Ajax": "AFC Ajax",
    "Monaco": "AS Monaco FC",
    "Frankfurt": "Eintracht Frankfurt",
}


def _normalize_espn_match(event: dict, league_name: str) -> Dict:
    """Convert ESPN event to our internal normalized format."""
    comps = event.get("competitions", [{}])[0]
    teams = comps.get("competitors", [])

    home_team = away_team = ""
    home_score = away_score = None
    for t in teams:
        name = t.get("team", {}).get("displayName", "?")
        name = TEAM_ALIASES.get(name, name)
        score = t.get("score")
        if t.get("homeAway") == "home":
            home_team = name
            home_score = int(score) if score and score != "?" else None
        else:
            away_team = name
            away_score = int(score) if score and score != "?" else None

    status_name = event.get("status", {}).get("type", {}).get("name", "")
    api_status = STATUS_MAP.get(status_name, status_name)

    return {
        "fixture": {
            "id": f"espn_{event.get('id', '0')}",
            "date": event.get("date", ""),
            "status": {"short": api_status},
        },
        "league": {
            "id": league_name,
            "name": league_name,
        },
        "teams": {
            "home": {"name": home_team},
            "away": {"name": away_team},
        },
        "goals": {
            "home": home_score,
            "away": away_score,
        },
    }


async def _fetch_one_competition(
    client: httpx.AsyncClient, espn_code: str, league_name: str, date_range: str,
) -> List[Dict]:
    """Fetch a single ESPN competition. Returns normalized matches."""
    try:
        url = f"{ESPN_BASE}/{espn_code}/scoreboard"
        params = {"dates": date_range, "limit": 900}
        response = await client.get(url, headers=HEADERS, params=params)
        response.raise_for_status()
        data = response.json()

        matches = []
        for e in data.get("events", []):
            m = _normalize_espn_match(e, league_name)
            if m["teams"]["home"]["name"] and m["teams"]["away"]["name"]:
                matches.append(m)

        logger.info(f"[ESPN] {league_name}: {len(matches)} matches")
        return matches

    except httpx.HTTPStatusError as e:
        logger.error(f"[ESPN] {league_name} HTTP {e.response.status_code}")
        return []
    except Exception as e:
        logger.error(f"[ESPN] {league_name} error: {e}")
        return []


async def fetch_espn_matches(db_has_data: bool = False) -> List[Dict]:
    """Fetch all ESPN competitions concurrently.

    When db_has_data=True, uses a shorter 30-day lookback (just updates).
    When False (initial load), uses full 6-month range for history.
    ESPN has no rate limit, so all 9 competitions fire in parallel.
    """
    today = datetime.now()
    if db_has_data:
        date_from = (today - timedelta(days=30)).strftime("%Y%m%d")
    else:
        season_start = "20250901"
        six_months_ago = (today - timedelta(days=180)).strftime("%Y%m%d")
        date_from = max(season_start, six_months_ago)
    date_to = (today + timedelta(days=14)).strftime("%Y%m%d")
    date_range = f"{date_from}-{date_to}"

    logger.info(f"[ESPN] Fetching {len(COMPETITIONS)} competitions in parallel ({date_from}→{date_to})")

    async with httpx.AsyncClient(timeout=20.0) as client:
        tasks = [
            _fetch_one_competition(client, code, name, date_range)
            for code, name in COMPETITIONS.items()
        ]
        results = await asyncio.gather(*tasks)

    # Flatten + dedup by fixture ID
    seen_ids: set = set()
    all_matches: List[Dict] = []
    for matches in results:
        for m in matches:
            fid = m["fixture"]["id"]
            if fid not in seen_ids:
                seen_ids.add(fid)
                all_matches.append(m)

    finished = sum(1 for m in all_matches if m["fixture"]["status"]["short"] == "FT")
    upcoming = sum(1 for m in all_matches if m["fixture"]["status"]["short"] == "NS")
    logger.info(f"[ESPN] Total: {len(all_matches)} matches ({finished} finished, {upcoming} upcoming)")

    return all_matches
