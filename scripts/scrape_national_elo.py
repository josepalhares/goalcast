#!/usr/bin/env python3
"""Scrape current national team Elo ratings from eloratings.net.

Fetches /World.tsv (public, no auth needed) and saves to data/national_elo.json.
Run manually or via GitHub Actions weekly.

Usage:
    python3 scripts/scrape_national_elo.py
"""
import json
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://www.eloratings.net/World.tsv"
OUTPUT = Path(__file__).parent.parent / "data" / "national_elo.json"

# eloratings.net uses its own 2-letter codes (not ISO 3166).
# This maps every code to the English team name used by ESPN / football-data.org.
CODE_TO_NAME = {
    "AB": "Abkhazia", "AD": "Andorra", "AE": "United Arab Emirates",
    "AF": "Afghanistan", "AG": "Antigua and Barbuda", "AI": "Anguilla",
    "AL": "Albania", "AM": "Armenia", "AO": "Angola", "AR": "Argentina",
    "AS": "American Samoa", "AT": "Austria", "AU": "Australia", "AW": "Aruba",
    "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina", "BB": "Barbados",
    "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso", "BG": "Bulgaria",
    "BH": "Bahrain", "BI": "Burundi", "BJ": "Benin", "BL": "Brunei",
    "BM": "Bermuda", "BN": "Brunei", "BO": "Bolivia", "BQ": "Bonaire",
    "BR": "Brazil", "BS": "Bahamas", "BT": "Bhutan", "BW": "Botswana",
    "BY": "Belarus", "BZ": "Belize", "CA": "Canada", "CC": "Cocos Islands",
    "CD": "DR Congo", "CF": "Central African Republic", "CG": "Congo",
    "CH": "Switzerland", "CI": "Ivory Coast", "CK": "Cook Islands",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CV": "Cape Verde", "CW": "Curacao",
    "CX": "Christmas Island", "CY": "Cyprus", "CZ": "Czech Republic",
    "DE": "Germany", "DJ": "Djibouti", "DK": "Denmark", "DM": "Dominica",
    "DO": "Dominican Republic", "DZ": "Algeria",
    "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt",
    "EH": "Western Sahara", "EI": "Northern Ireland", "EN": "England",
    "ER": "Eritrea", "ES": "Spain", "ET": "Ethiopia", "EU": "Reunion",
    "FI": "Finland", "FJ": "Fiji", "FK": "Falkland Islands", "FM": "Micronesia",
    "FO": "Faroe Islands", "FR": "France",
    "GA": "Gabon", "GD": "Grenada", "GE": "Georgia", "GF": "French Guiana",
    "GH": "Ghana", "GI": "Gibraltar", "GL": "Greenland", "GM": "Gambia",
    "GN": "Guinea", "GP": "Guadeloupe", "GQ": "Equatorial Guinea",
    "GR": "Greece", "GT": "Guatemala", "GU": "Guam", "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HG": "Hong Kong", "HK": "Hong Kong", "HN": "Honduras", "HR": "Croatia",
    "HT": "Haiti", "HU": "Hungary",
    "ID": "Indonesia", "IE": "Republic of Ireland", "IL": "Israel",
    "IN": "India", "IQ": "Iraq", "IR": "Iran", "IS": "Iceland", "IT": "Italy",
    "JM": "Jamaica", "JO": "Jordan", "JP": "Japan", "JS": "Jersey",
    "KD": "Kurdistan", "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia",
    "KI": "Kiribati", "KM": "Comoros", "KN": "Saint Kitts and Nevis",
    "KO": "Kosovo", "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait",
    "KY": "Cayman Islands", "KZ": "Kazakhstan",
    "LA": "Laos", "LB": "Lebanon", "LC": "Saint Lucia", "LI": "Liechtenstein",
    "LK": "Sri Lanka", "LR": "Liberia", "LS": "Lesotho", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya",
    "MA": "Morocco", "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro",
    "MF": "Saint Martin", "MG": "Madagascar", "MH": "Marshall Islands",
    "MK": "North Macedonia", "ML": "Mali", "MM": "Myanmar", "MN": "Mongolia",
    "MO": "Macau", "MP": "Northern Mariana Islands", "MQ": "Martinique",
    "MR": "Mauritania", "MS": "Montserrat", "MT": "Malta", "MU": "Mauritius",
    "MV": "Maldives", "MW": "Malawi", "MX": "Mexico", "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia", "NC": "New Caledonia", "NE": "Niger", "NG": "Nigeria",
    "NI": "Nicaragua", "NL": "Netherlands", "NM": "North Macedonia",
    "NO": "Norway", "NP": "Nepal", "NS": "Suriname", "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama", "PE": "Peru", "PG": "Papua New Guinea", "PH": "Philippines",
    "PK": "Pakistan", "PL": "Poland", "PM": "Saint Pierre and Miquelon",
    "PR": "Puerto Rico", "PS": "Palestine", "PT": "Portugal", "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RE": "Reunion", "RO": "Romania", "RS": "Serbia", "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia", "SB": "Solomon Islands", "SC": "Seychelles",
    "SD": "Sudan", "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
    "SK": "Slovakia", "SL": "Sierra Leone", "SM": "San Marino",
    "SN": "Senegal", "SO": "Somalia", "SQ": "Albania", "SR": "Suriname",
    "SS": "South Sudan", "ST": "Sao Tome and Principe", "SV": "El Salvador",
    "SW": "Eswatini", "SX": "Sint Maarten", "SY": "Syria",
    "TC": "Turks and Caicos Islands", "TD": "Chad",
    "TE": "Turks and Caicos Islands", "TG": "Togo",
    "TH": "Thailand", "TI": "Timor-Leste", "TJ": "Tajikistan",
    "TL": "Timor-Leste", "TM": "Turkmenistan", "TN": "Tunisia",
    "TO": "Tonga", "TR": "Turkey", "TT": "Trinidad and Tobago",
    "TV": "Tuvalu", "TW": "Taiwan", "TZ": "Tanzania",
    "UA": "Ukraine", "UG": "Uganda", "US": "United States",
    "UY": "Uruguay", "UZ": "Uzbekistan",
    "VA": "US Virgin Islands", "VC": "Saint Vincent and the Grenadines",
    "VE": "Venezuela", "VG": "British Virgin Islands", "VI": "US Virgin Islands",
    "VN": "Vietnam", "VU": "Vanuatu",
    "WA": "Wales", "WF": "Wallis and Futuna", "WS": "Samoa",
    "YE": "Yemen", "YT": "Mayotte",
    "ZA": "South Africa", "ZM": "Zambia", "ZN": "Zanzibar", "ZW": "Zimbabwe",
}

# Some eloratings codes map to duplicate names (NM and MK both = North Macedonia,
# SQ = Albania but AL also = Albania). We keep both — later dedup by name.


def scrape() -> dict:
    """Fetch World.tsv and return {name: elo} dict."""
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8")

    ratings: dict[str, int] = {}
    for line in text.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        code = parts[2]
        try:
            elo = int(parts[3])
        except ValueError:
            continue

        name = CODE_TO_NAME.get(code)
        if not name:
            print(f"  Unknown code: {code} (elo={elo})")
            continue

        # Keep highest Elo if duplicate names (e.g. NM and MK both → North Macedonia)
        if name not in ratings or elo > ratings[name]:
            ratings[name] = elo

    return ratings


def main():
    print("Fetching national team Elo ratings from eloratings.net...")
    ratings = scrape()
    print(f"Fetched {len(ratings)} teams")

    # Show top 20
    top = sorted(ratings.items(), key=lambda x: -x[1])[:20]
    for name, elo in top:
        print(f"  {name}: {elo}")

    data = {
        "source": "eloratings.net",
        "fetched": str(date.today()),
        "ratings": dict(sorted(ratings.items(), key=lambda x: -x[1])),
    }

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(data, indent=2))
    print(f"\nSaved {len(ratings)} ratings to {OUTPUT}")


if __name__ == "__main__":
    main()
