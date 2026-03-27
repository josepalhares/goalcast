"""National team Elo ratings for international football predictions.

ClubElo only tracks club teams. This module provides Elo ratings for national
teams sourced from eloratings.net (scraped weekly via scripts/scrape_national_elo.py).

Loads from data/national_elo.json if available, otherwise falls back to a
hardcoded snapshot. Teams below the ELO_THRESHOLD are excluded — this naturally
filters tiny nations from friendlies since _find_elo() returns None and
do_refresh() skips them.
"""
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ELO_THRESHOLD = 800  # Skip only the ~30 smallest territories (keeps all WC qualifier + Nations League teams)

_JSON_PATH = Path(__file__).parent.parent / "data" / "national_elo.json"

# Hardcoded fallback (approximate, used only if JSON is missing)
_FALLBACK_ELO: dict[str, float] = {
    "Spain": 2172, "Argentina": 2113, "France": 2070, "England": 2042,
    "Colombia": 1986, "Portugal": 1976, "Brazil": 1970, "Netherlands": 1959,
    "Croatia": 1944, "Ecuador": 1933, "Norway": 1922, "Germany": 1910,
    "Switzerland": 1897, "Uruguay": 1890, "Turkey": 1885, "Japan": 1878,
    "Denmark": 1872, "Senegal": 1869, "Italy": 1866, "Mexico": 1857,
    "Belgium": 1850, "Paraguay": 1833, "Austria": 1818, "Morocco": 1806,
    "Canada": 1805, "Albania": 1790, "South Korea": 1784, "Australia": 1774,
    "Serbia": 1768, "Greece": 1761, "Ukraine": 1760, "Iran": 1755,
    "United States": 1747, "Poland": 1746, "Chile": 1743, "Nigeria": 1739,
    "Kosovo": 1738, "Panama": 1733, "Algeria": 1728, "Uzbekistan": 1728,
    "Czech Republic": 1723, "Venezuela": 1715, "Peru": 1708, "Wales": 1703,
    "Sweden": 1702, "Hungary": 1698, "Republic of Ireland": 1696,
    "Slovenia": 1695, "Jordan": 1689, "Bolivia": 1670, "Slovakia": 1663,
    "Egypt": 1659, "DR Congo": 1640, "Romania": 1637, "Ivory Coast": 1637,
    "Israel": 1634, "Costa Rica": 1632, "Tunisia": 1614, "Cameroon": 1606,
    "Northern Ireland": 1595, "Saudi Arabia": 1592, "Mali": 1589,
    "North Macedonia": 1584, "Bosnia and Herzegovina": 1584, "Iraq": 1582,
    "Honduras": 1567, "Iceland": 1562, "New Zealand": 1552, "Jamaica": 1550,
    "Cape Verde": 1549, "Haiti": 1542, "Finland": 1541,
    "United Arab Emirates": 1540, "South Africa": 1528, "Ghana": 1509,
    "Belarus": 1503, "Oman": 1490, "Guinea": 1486, "Syria": 1486,
    "Palestine": 1470, "Suriname": 1457, "Bulgaria": 1453, "Montenegro": 1443,
    "Curacao": 1440, "China": 1436, "Libya": 1425, "Qatar": 1425,
    "Gambia": 1424, "Luxembourg": 1424, "Kazakhstan": 1421, "Bahrain": 1418,
    "Benin": 1410, "Gabon": 1405, "Niger": 1404, "Trinidad and Tobago": 1399,
    "Uganda": 1394, "Equatorial Guinea": 1391, "Armenia": 1389,
    "Faroe Islands": 1381, "North Korea": 1375, "Comoros": 1374,
    "Mozambique": 1372, "Zambia": 1370, "Madagascar": 1368, "Thailand": 1368,
    "Estonia": 1359, "Sudan": 1350, "Sierra Leone": 1344, "Kenya": 1344,
    "Zimbabwe": 1342, "Togo": 1342, "Indonesia": 1341, "Azerbaijan": 1339,
    "Lebanon": 1332, "Vietnam": 1331, "Kuwait": 1328, "Nicaragua": 1328,
    "El Salvador": 1327, "Malaysia": 1313, "Tanzania": 1312, "Mauritania": 1311,
    "Rwanda": 1308, "Kyrgyzstan": 1305, "Namibia": 1300, "Liberia": 1299,
    "Tajikistan": 1297, "Latvia": 1297, "Lithuania": 1297, "Cyprus": 1295,
    "Dominican Republic": 1290, "New Caledonia": 1286, "Botswana": 1285,
    "Guyana": 1275, "Moldova": 1268, "Ethiopia": 1264, "Malawi": 1252,
    "Malta": 1249, "Guinea-Bissau": 1244, "Turkmenistan": 1217,
    "Lesotho": 1200,
}


def _load_elo_dict() -> dict[str, float]:
    """Load ratings from JSON file, filtering by threshold. Falls back to hardcoded."""
    if _JSON_PATH.exists():
        try:
            data = json.loads(_JSON_PATH.read_text())
            ratings = data.get("ratings", {})
            filtered = {k: v for k, v in ratings.items() if v >= ELO_THRESHOLD}
            logger.info(f"Loaded {len(filtered)} national team Elo ratings from {_JSON_PATH} (fetched {data.get('fetched', '?')})")
            return filtered
        except Exception as e:
            logger.warning(f"Failed to load {_JSON_PATH}: {e}, using fallback")
    return dict(_FALLBACK_ELO)


NATIONAL_TEAM_ELO: dict[str, float] = _load_elo_dict()

# Variant names → canonical key in NATIONAL_TEAM_ELO
NATIONAL_TEAM_ALIASES: dict[str, str] = {
    # ESPN variants
    "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Korea Republic": "South Korea", "S. Korea": "South Korea",
    "USA": "United States", "United States of America": "United States",
    "IR Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast", "Cote d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo", "Dem. Rep. Congo": "DR Congo",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Bosnia": "Bosnia and Herzegovina",
    "Czechia": "Czech Republic",
    "Eire": "Republic of Ireland", "Ireland": "Republic of Ireland",
    "Rep. of Ireland": "Republic of Ireland", "Rep of Ireland": "Republic of Ireland",
    "FYR Macedonia": "North Macedonia", "N. Macedonia": "North Macedonia",
    "N. Ireland": "Northern Ireland",
    "UAE": "United Arab Emirates",
    "Kyrgyz Republic": "Kyrgyzstan",
}


def get_national_elo(team_name: str) -> Optional[float]:
    """Look up national team Elo by name (tries direct match then aliases).

    Returns None if not found — this intentionally filters out tiny nations.
    """
    if team_name in NATIONAL_TEAM_ELO:
        return NATIONAL_TEAM_ELO[team_name]

    canonical = NATIONAL_TEAM_ALIASES.get(team_name)
    if canonical and canonical in NATIONAL_TEAM_ELO:
        return NATIONAL_TEAM_ELO[canonical]

    name_lower = team_name.lower()
    for key, elo in NATIONAL_TEAM_ELO.items():
        if key.lower() == name_lower:
            return elo

    for alias, canonical in NATIONAL_TEAM_ALIASES.items():
        if alias.lower() == name_lower and canonical in NATIONAL_TEAM_ELO:
            return NATIONAL_TEAM_ELO[canonical]

    return None
