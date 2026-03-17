"""ClubElo API client for fetching team Elo ratings."""
import httpx
from datetime import datetime
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

CLUBELO_API_BASE = "http://api.clubelo.com"


async def fetch_elo_ratings(date: Optional[str] = None) -> Dict[str, float]:
    """
    Fetch Elo ratings for all clubs from ClubElo API.

    Args:
        date: Date in YYYY-MM-DD format. If None, uses today's date.

    Returns:
        Dict mapping team name to Elo rating.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    url = f"{CLUBELO_API_BASE}/{date}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            # ClubElo returns CSV format
            lines = response.text.strip().split("\n")

            if not lines:
                logger.warning(f"No data returned from ClubElo for date {date}")
                return {}

            # First line is header
            header = lines[0].split(",")

            # Find column indices
            club_idx = header.index("Club")
            elo_idx = header.index("Elo")

            # Parse data
            elo_ratings = {}
            for line in lines[1:]:
                fields = line.split(",")
                if len(fields) > max(club_idx, elo_idx):
                    club = fields[club_idx].strip()
                    elo = float(fields[elo_idx].strip())
                    elo_ratings[club] = elo

            logger.info(f"Fetched {len(elo_ratings)} Elo ratings for date {date}")
            return elo_ratings

    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching ClubElo data: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error fetching ClubElo data: {e}")
        return {}


async def get_team_elo(team_name: str, date: Optional[str] = None) -> Optional[float]:
    """
    Get Elo rating for a specific team.

    Args:
        team_name: Name of the team.
        date: Date in YYYY-MM-DD format. If None, uses today's date.

    Returns:
        Elo rating or None if not found.
    """
    elo_ratings = await fetch_elo_ratings(date)

    # Try exact match first
    if team_name in elo_ratings:
        return elo_ratings[team_name]

    # Try case-insensitive match
    team_lower = team_name.lower()
    for club, elo in elo_ratings.items():
        if club.lower() == team_lower:
            return elo

    # Try partial match
    for club, elo in elo_ratings.items():
        if team_lower in club.lower() or club.lower() in team_lower:
            logger.info(f"Partial match: {team_name} -> {club}")
            return elo

    logger.warning(f"No Elo rating found for team: {team_name}")
    return None
