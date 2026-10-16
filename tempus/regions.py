"""Server country -> region, for grouping the board. Tempus gives each server a country name (sometimes empty)."""
from __future__ import annotations

from typing import Optional

EUROPE = "Europe"
NORTH_AMERICA = "North America"
SOUTH_AMERICA = "South America"
ASIA = "Asia"
OCEANIA = "Oceania"
MIDDLE_EAST = "Middle East"
AFRICA = "Africa"
OTHER = "Other"
REGIONS = (EUROPE, NORTH_AMERICA, SOUTH_AMERICA, ASIA, OCEANIA, MIDDLE_EAST, AFRICA)

_TABLE = {
    EUROPE: ("Austria", "Belgium", "Bulgaria", "Croatia", "Czechia", "Czech Republic", "Denmark", "Estonia", "Finland", "France", "Germany",
             "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Netherlands", "The Netherlands", "Norway",
             "Poland", "Portugal", "Romania", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "Ukraine", "United Kingdom",
             # Moscow and Novosibirsk alike: the RG servers are played as a European group
             "Russia", "Russian Federation", "Turkey", "Türkiye", "Belarus", "Moldova"),
    NORTH_AMERICA: ("United States", "USA", "Canada", "Mexico"),
    SOUTH_AMERICA: ("Brazil", "Brasil", "Argentina", "Chile", "Colombia", "Peru", "Uruguay", "Paraguay", "Ecuador", "Venezuela", "Bolivia"),
    ASIA: ("Singapore", "Japan", "Hong Kong", "South Korea", "Korea", "Taiwan", "China", "India", "Thailand", "Malaysia", "Indonesia",
           "Philippines", "Vietnam", "Kazakhstan", "Mongolia"),
    OCEANIA: ("Australia", "New Zealand"),
    MIDDLE_EAST: ("Israel", "United Arab Emirates", "UAE", "Saudi Arabia", "Bahrain", "Qatar", "Kuwait", "Oman", "Jordan", "Lebanon", "Iran", "Iraq"),
    AFRICA: ("South Africa", "Egypt", "Nigeria", "Kenya", "Morocco", "Tunisia", "Algeria", "Ghana"),
}
_BY_COUNTRY = {country.casefold(): region for region, countries in _TABLE.items() for country in countries}


def region_for(country: Optional[str]) -> str:
    return _BY_COUNTRY.get((country or "").strip().casefold(), OTHER)


# server countries come as names; flags need the ISO code
_CODES = {
    "Australia": "AU", "Austria": "AT", "Belgium": "BE", "Brazil": "BR", "Brasil": "BR", "Bulgaria": "BG", "Canada": "CA", "Chile": "CL",
    "Argentina": "AR", "China": "CN", "Colombia": "CO", "Croatia": "HR", "Czechia": "CZ", "Czech Republic": "CZ", "Denmark": "DK",
    "Estonia": "EE", "Finland": "FI", "France": "FR", "Germany": "DE", "Greece": "GR", "Hong Kong": "HK", "Hungary": "HU", "India": "IN",
    "Indonesia": "ID", "Ireland": "IE", "Israel": "IL", "Italy": "IT", "Japan": "JP", "Kazakhstan": "KZ", "Latvia": "LV", "Lithuania": "LT",
    "Malaysia": "MY", "Mexico": "MX", "Netherlands": "NL", "The Netherlands": "NL", "New Zealand": "NZ", "Norway": "NO", "Peru": "PE",
    "Philippines": "PH", "Poland": "PL", "Portugal": "PT", "Romania": "RO", "Russia": "RU", "Russian Federation": "RU", "Saudi Arabia": "SA",
    "Serbia": "RS", "Singapore": "SG", "Slovakia": "SK", "South Africa": "ZA", "South Korea": "KR", "Korea": "KR", "Spain": "ES",
    "Sweden": "SE", "Switzerland": "CH", "Taiwan": "TW", "Thailand": "TH", "Turkey": "TR", "Türkiye": "TR", "Ukraine": "UA",
    "United Arab Emirates": "AE", "UAE": "AE", "United Kingdom": "GB", "United States": "US", "USA": "US", "Vietnam": "VN",
}
_CODE_BY_COUNTRY = {name.casefold(): code for name, code in _CODES.items()}
NO_FLAG = "🌐"


def flag(country: Optional[str] = None, code: Optional[str] = None) -> str:
    """Flag emoji for a country name or ISO code, or a globe when it's unknown."""
    code = (code or _CODE_BY_COUNTRY.get((country or "").strip().casefold()) or "").upper()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return NO_FLAG
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)
