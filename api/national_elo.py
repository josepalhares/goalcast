"""National team Elo ratings for international football predictions.

ClubElo only tracks club teams. This module provides approximate Elo ratings
for national teams so international matches aren't silently skipped.

Ratings are approximate values on the ClubElo scale (~1700-2150).
Teams below 1700 are intentionally excluded — this naturally filters tiny
nations from friendlies since _find_elo() returns None and do_refresh() skips them.

Source: approximate values calibrated to eloratings.net (March 2026).
Update quarterly or after major tournaments.
"""
from typing import Optional

# ~80 national teams, roughly top-50 per confederation
# Scale: ClubElo-compatible (top clubs ~2050, top nations ~2100)
NATIONAL_TEAM_ELO: dict[str, float] = {
    # UEFA
    "France": 2080,
    "Spain": 2060,
    "England": 2040,
    "Portugal": 2030,
    "Germany": 2010,
    "Netherlands": 2000,
    "Belgium": 1970,
    "Italy": 1980,
    "Croatia": 1940,
    "Switzerland": 1900,
    "Denmark": 1890,
    "Austria": 1870,
    "Turkey": 1860,
    "Ukraine": 1850,
    "Sweden": 1840,
    "Serbia": 1840,
    "Poland": 1830,
    "Czech Republic": 1820,
    "Romania": 1810,
    "Hungary": 1810,
    "Scotland": 1800,
    "Norway": 1800,
    "Greece": 1790,
    "Wales": 1780,
    "Slovakia": 1780,
    "Republic of Ireland": 1770,
    "Finland": 1760,
    "Albania": 1760,
    "North Macedonia": 1740,
    "Iceland": 1740,
    "Bosnia and Herzegovina": 1750,
    "Slovenia": 1750,
    "Georgia": 1750,
    "Montenegro": 1730,
    "Northern Ireland": 1720,
    "Bulgaria": 1720,
    "Luxembourg": 1710,
    "Kosovo": 1710,
    # CONMEBOL
    "Argentina": 2120,
    "Brazil": 2100,
    "Uruguay": 1950,
    "Colombia": 1920,
    "Ecuador": 1850,
    "Chile": 1830,
    "Paraguay": 1780,
    "Peru": 1770,
    "Venezuela": 1750,
    "Bolivia": 1710,
    # CONCACAF
    "Mexico": 1870,
    "United States": 1860,
    "Canada": 1800,
    "Costa Rica": 1760,
    "Panama": 1740,
    "Jamaica": 1720,
    "Honduras": 1710,
    # AFC
    "Japan": 1880,
    "South Korea": 1860,
    "Iran": 1840,
    "Australia": 1820,
    "Saudi Arabia": 1790,
    "Qatar": 1750,
    "Iraq": 1740,
    "Uzbekistan": 1730,
    "China": 1720,
    "United Arab Emirates": 1720,
    # CAF
    "Morocco": 1900,
    "Senegal": 1870,
    "Nigeria": 1840,
    "Egypt": 1830,
    "Ivory Coast": 1820,
    "Cameroon": 1800,
    "Algeria": 1790,
    "Tunisia": 1780,
    "Ghana": 1770,
    "South Africa": 1740,
    "Mali": 1760,
    "DR Congo": 1740,
}

# Variant names → canonical key in NATIONAL_TEAM_ELO
NATIONAL_TEAM_ALIASES: dict[str, str] = {
    # ESPN variants
    "Türkiye": "Turkey",
    "Korea Republic": "South Korea",
    "USA": "United States",
    "IR Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "Dem. Rep. Congo": "DR Congo",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Czech Republic": "Czech Republic",
    "Czechia": "Czech Republic",
    # football-data.org variants
    "Turkiye": "Turkey",
    "Korea, South": "South Korea",
    "Korea DPR": "North Korea",
    "United States of America": "United States",
    "Eire": "Republic of Ireland",
    "Ireland": "Republic of Ireland",
    "North Macedonia": "North Macedonia",
    "FYR Macedonia": "North Macedonia",
    "Chinese Taipei": "Taiwan",
    "UAE": "United Arab Emirates",
    # Common short forms
    "S. Korea": "South Korea",
    "N. Ireland": "Northern Ireland",
    "N. Macedonia": "North Macedonia",
    "Bosnia": "Bosnia and Herzegovina",
    "DR Congo": "DR Congo",
    "Rep. of Ireland": "Republic of Ireland",
    "Rep of Ireland": "Republic of Ireland",
}


def get_national_elo(team_name: str) -> Optional[float]:
    """Look up national team Elo by name (tries direct match then aliases).

    Returns None if not found — this intentionally filters out tiny nations.
    """
    # Direct lookup
    if team_name in NATIONAL_TEAM_ELO:
        return NATIONAL_TEAM_ELO[team_name]

    # Via alias
    canonical = NATIONAL_TEAM_ALIASES.get(team_name)
    if canonical and canonical in NATIONAL_TEAM_ELO:
        return NATIONAL_TEAM_ELO[canonical]

    # Case-insensitive fallback
    name_lower = team_name.lower()
    for key, elo in NATIONAL_TEAM_ELO.items():
        if key.lower() == name_lower:
            return elo

    for alias, canonical in NATIONAL_TEAM_ALIASES.items():
        if alias.lower() == name_lower and canonical in NATIONAL_TEAM_ELO:
            return NATIONAL_TEAM_ELO[canonical]

    return None
