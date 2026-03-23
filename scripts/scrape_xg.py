#!/usr/bin/env python3
"""Scrape team xG data from Understat.com using Playwright.

Run locally (not on Railway — needs headless browser):
    pip install playwright
    playwright install chromium
    python scripts/scrape_xg.py

Outputs: data/xg_data.json
"""
import asyncio
import json
import re
import sys
from pathlib import Path
from datetime import datetime

# Understat league codes → our league names
LEAGUES = {
    "EPL": "Premier League",
    "La_liga": "La Liga",
    "Serie_A": "Serie A",
    "Bundesliga": "Bundesliga",
    "Ligue_1": "Ligue 1",
    # Understat doesn't cover Liga Portugal or Champions League
}

# Understat team name → our internal name (football-data.org names)
TEAM_ALIASES = {
    # EPL
    "Manchester City": "Manchester City FC",
    "Arsenal": "Arsenal FC",
    "Liverpool": "Liverpool FC",
    "Chelsea": "Chelsea FC",
    "Manchester United": "Manchester United FC",
    "Tottenham": "Tottenham Hotspur FC",
    "Newcastle United": "Newcastle United FC",
    "Aston Villa": "Aston Villa FC",
    "Brighton": "Brighton & Hove Albion FC",
    "West Ham": "West Ham United FC",
    "Bournemouth": "AFC Bournemouth",
    "Crystal Palace": "Crystal Palace FC",
    "Brentford": "Brentford FC",
    "Fulham": "Fulham FC",
    "Wolverhampton Wanderers": "Wolverhampton Wanderers FC",
    "Everton": "Everton FC",
    "Nottingham Forest": "Nottingham Forest FC",
    "Leicester": "Leicester City FC",
    "Ipswich": "Ipswich Town FC",
    "Southampton": "Southampton FC",
    "Leeds": "Leeds United FC",
    "Burnley": "Burnley FC",
    "Sunderland": "Sunderland AFC",
    # La Liga
    "Barcelona": "FC Barcelona",
    "Real Madrid": "Real Madrid CF",
    "Atletico Madrid": "Club Atlético de Madrid",
    "Real Sociedad": "Real Sociedad de Fútbol",
    "Real Betis": "Real Betis Balompié",
    "Villarreal": "Villarreal CF",
    "Athletic Club": "Athletic Club",
    "Sevilla": "Sevilla FC",
    "Valencia": "Valencia CF",
    "Celta Vigo": "RC Celta de Vigo",
    "Osasuna": "CA Osasuna",
    "Getafe": "Getafe CF",
    "Mallorca": "RCD Mallorca",
    "Rayo Vallecano": "Rayo Vallecano de Madrid",
    "Girona": "Girona FC",
    "Espanyol": "RCD Espanyol de Barcelona",
    "Alaves": "Deportivo Alavés",
    "Levante": "Levante UD",
    "Elche": "Elche CF",
    "Real Oviedo": "Real Oviedo",
    # Serie A
    "Napoli": "SSC Napoli",
    "Inter": "FC Internazionale Milano",
    "AC Milan": "AC Milan",
    "Juventus": "Juventus FC",
    "Atalanta": "Atalanta BC",
    "Roma": "AS Roma",
    "Lazio": "SS Lazio",
    "Fiorentina": "ACF Fiorentina",
    "Bologna": "Bologna FC 1909",
    "Torino": "Torino FC",
    "Genoa": "Genoa CFC",
    "Udinese": "Udinese Calcio",
    "Cagliari": "Cagliari Calcio",
    "Sassuolo": "US Sassuolo Calcio",
    "Lecce": "US Lecce",
    "Verona": "Hellas Verona FC",
    "Parma Calcio 1913": "Parma Calcio 1913",
    "Como": "Como 1907",
    "Cremonese": "US Cremonese",
    "Pisa": "AC Pisa 1909",
    # Bundesliga
    "Bayern Munich": "FC Bayern München",
    "Borussia Dortmund": "Borussia Dortmund",
    "Bayer Leverkusen": "Bayer 04 Leverkusen",
    "RB Leipzig": "RB Leipzig",
    "Stuttgart": "VfB Stuttgart",
    "Eintracht Frankfurt": "Eintracht Frankfurt",
    "Freiburg": "SC Freiburg",
    "Union Berlin": "1. FC Union Berlin",
    "Wolfsburg": "VfL Wolfsburg",
    "Borussia M.Gladbach": "Borussia Mönchengladbach",
    "Mainz 05": "1. FSV Mainz 05",
    "Hoffenheim": "TSG 1899 Hoffenheim",
    "Augsburg": "FC Augsburg",
    "Werder Bremen": "SV Werder Bremen",
    "Heidenheim": "1. FC Heidenheim 1846",
    "St. Pauli": "FC St. Pauli 1910",
    "Koln": "1. FC Köln",
    "Hamburg": "Hamburger SV",
    # Ligue 1
    "Paris Saint Germain": "Paris Saint-Germain FC",
    "Marseille": "Olympique de Marseille",
    "Lyon": "Olympique Lyonnais",
    "Monaco": "AS Monaco FC",
    "Lille": "LOSC Lille",
    "Nice": "OGC Nice",
    "Rennes": "Stade Rennais FC 1901",
    "Lens": "Racing Club de Lens",
    "Strasbourg": "RC Strasbourg Alsace",
    "Toulouse": "Toulouse FC",
    "Nantes": "FC Nantes",
    "Reims": "Stade de Reims",
    "Brest": "Stade Brestois 29",
    "Montpellier": "Montpellier HSC",
    "Le Havre": "Le Havre AC",
    "Auxerre": "AJ Auxerre",
    "Angers": "Angers SCO",
    "Saint-Etienne": "AS Saint-Étienne",
    "Metz": "FC Metz",
    "Lorient": "FC Lorient",
    "Paris FC": "Paris FC",
}


async def scrape_league(page, league_code: str, season: str = "2025") -> list:
    """Scrape xG data for a single league from Understat."""
    url = f"https://understat.com/league/{league_code}/{season}"
    print(f"  Scraping {league_code}... ", end="", flush=True)

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(2000)  # Extra wait for JS

    # Extract teamsData from the page's JavaScript context
    data = await page.evaluate("""
    () => {
        // Understat stores data in script tags as JSON.parse('...')
        const scripts = document.querySelectorAll('script');
        for (const script of scripts) {
            const text = script.textContent;
            const match = text.match(/var\\s+teamsData\\s*=\\s*JSON\\.parse\\('(.+?)'\\)/);
            if (match) {
                return JSON.parse(match[1].replace(/\\\\x([0-9a-fA-F]{2})/g,
                    (_, hex) => String.fromCharCode(parseInt(hex, 16))));
            }
        }
        return null;
    }
    """)

    if not data:
        print("FAILED — no teamsData found")
        return []

    teams = []
    for team_id, team in data.items():
        title = team.get("title", "")
        history = team.get("history", [])

        if not history:
            continue

        # Calculate season totals from match history
        xg_for = sum(float(m.get("xG", 0)) for m in history)
        xg_against = sum(float(m.get("xGA", 0)) for m in history)
        matches_played = len(history)

        # Home/away splits
        home_matches = [m for m in history if m.get("h_a") == "h"]
        away_matches = [m for m in history if m.get("h_a") == "a"]

        xg_for_home = sum(float(m.get("xG", 0)) for m in home_matches)
        xg_against_home = sum(float(m.get("xGA", 0)) for m in home_matches)
        xg_for_away = sum(float(m.get("xG", 0)) for m in away_matches)
        xg_against_away = sum(float(m.get("xGA", 0)) for m in away_matches)

        # Map to our internal team name
        internal_name = TEAM_ALIASES.get(title, title)

        teams.append({
            "understat_name": title,
            "team_name": internal_name,
            "league": LEAGUES[league_code],
            "xg_for": round(xg_for, 2),
            "xg_against": round(xg_against, 2),
            "xg_for_per_match": round(xg_for / matches_played, 2) if matches_played else 0,
            "xg_against_per_match": round(xg_against / matches_played, 2) if matches_played else 0,
            "xg_for_home": round(xg_for_home, 2),
            "xg_against_home": round(xg_against_home, 2),
            "xg_for_away": round(xg_for_away, 2),
            "xg_against_away": round(xg_against_away, 2),
            "matches_played": matches_played,
            "home_matches": len(home_matches),
            "away_matches": len(away_matches),
        })

    print(f"{len(teams)} teams")
    return teams


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Install Playwright first:")
        print("  pip install playwright")
        print("  playwright install chromium")
        sys.exit(1)

    output_path = Path(__file__).parent.parent / "data" / "xg_data.json"
    output_path.parent.mkdir(exist_ok=True)

    print(f"Scraping Understat xG data...")
    print(f"Output: {output_path}")
    print()

    all_teams = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for league_code in LEAGUES:
            try:
                teams = await scrape_league(page, league_code)
                all_teams.extend(teams)
            except Exception as e:
                print(f"  ERROR scraping {league_code}: {e}")

            await page.wait_for_timeout(3000)  # Rate limit

        await browser.close()

    # Save
    result = {
        "scraped_at": datetime.utcnow().isoformat(),
        "season": "2025-2026",
        "teams": all_teams,
        "leagues_scraped": list(LEAGUES.values()),
    }

    output_path.write_text(json.dumps(result, indent=2))
    print(f"\nSaved {len(all_teams)} teams to {output_path}")

    # Summary
    for league in LEAGUES.values():
        league_teams = [t for t in all_teams if t["league"] == league]
        if league_teams:
            avg_xg = sum(t["xg_for_per_match"] for t in league_teams) / len(league_teams)
            print(f"  {league}: {len(league_teams)} teams, avg xG/match: {avg_xg:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
