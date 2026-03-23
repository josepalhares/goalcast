#!/usr/bin/env python3
"""Fetch team goals-per-match data from football-data.org standings.

This uses the free API (no scraping needed) to get GF/GA per match
for all teams across all leagues. Saved as data/xg_data.json.

While not actual xG, goals-per-match rates serve as a reliable
team strength signal for the Dixon-Coles model.

Run: python3 scripts/scrape_xg.py

Requires: FOOTBALL_DATA_KEY env var (or .env file)
"""
import asyncio
import httpx
import json
import os
import sys
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

API_BASE = "https://api.football-data.org/v4"

COMPETITIONS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "PPL": "Liga Portugal",
    "CL": "Champions League",
}

# football-data.org team names → our internal names (same mapping as routes.py)
# Only needed for teams whose standings name differs from match name
TEAM_ALIASES = {
    # Most names match already — add overrides here if needed
}


async def fetch_standings(client: httpx.AsyncClient, headers: dict, comp_code: str) -> list:
    """Fetch standings for a competition."""
    url = f"{API_BASE}/competitions/{comp_code}/standings"
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        standings = data.get("standings", [])
        if not standings:
            return []

        # For group-stage competitions (CL), flatten all groups
        teams = []
        for group in standings:
            for t in group.get("table", []):
                team_name = t["team"]["name"]
                team_name = TEAM_ALIASES.get(team_name, team_name)
                mp = t.get("playedGames", 0)
                if mp == 0:
                    continue
                gf = t.get("goalsFor", 0)
                ga = t.get("goalsAgainst", 0)

                teams.append({
                    "team_name": team_name,
                    "league": COMPETITIONS[comp_code],
                    "xg_for": round(gf, 2),  # Using actual goals as proxy
                    "xg_against": round(ga, 2),
                    "xg_for_per_match": round(gf / mp, 2),
                    "xg_against_per_match": round(ga / mp, 2),
                    # No home/away split available from standings
                    "xg_for_home": 0,
                    "xg_against_home": 0,
                    "xg_for_away": 0,
                    "xg_against_away": 0,
                    "matches_played": mp,
                    "home_matches": 0,
                    "away_matches": 0,
                    "source": "football-data.org standings (goals, not xG)",
                })

        return teams

    except httpx.HTTPStatusError as e:
        print(f"  ERROR {comp_code}: HTTP {e.response.status_code}")
        return []
    except Exception as e:
        print(f"  ERROR {comp_code}: {e}")
        return []


async def main():
    api_key = os.getenv("FOOTBALL_DATA_KEY")
    if not api_key:
        print("Set FOOTBALL_DATA_KEY env var or create .env file")
        sys.exit(1)

    output_path = Path(__file__).parent.parent / "data" / "xg_data.json"
    output_path.parent.mkdir(exist_ok=True)

    print("Fetching team strength data from football-data.org standings...")
    print(f"Output: {output_path}")
    print()

    headers = {"X-Auth-Token": api_key}
    all_teams = []

    async with httpx.AsyncClient(timeout=15.0) as client:
        for comp_code, comp_name in COMPETITIONS.items():
            print(f"  {comp_name}... ", end="", flush=True)
            teams = await fetch_standings(client, headers, comp_code)
            print(f"{len(teams)} teams")
            all_teams.extend(teams)
            await asyncio.sleep(7)  # Rate limit: 10 req/min

    result = {
        "scraped_at": datetime.utcnow().isoformat(),
        "season": "2025-2026",
        "source": "football-data.org standings (actual goals per match, not xG)",
        "teams": all_teams,
        "leagues_scraped": list(COMPETITIONS.values()),
    }

    output_path.write_text(json.dumps(result, indent=2))
    print(f"\nSaved {len(all_teams)} teams to {output_path}")

    for league in COMPETITIONS.values():
        league_teams = [t for t in all_teams if t["league"] == league]
        if league_teams:
            avg_gf = sum(t["xg_for_per_match"] for t in league_teams) / len(league_teams)
            avg_ga = sum(t["xg_against_per_match"] for t in league_teams) / len(league_teams)
            print(f"  {league:>20}: {len(league_teams)} teams, avg GF/m={avg_gf:.2f}, avg GA/m={avg_ga:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
