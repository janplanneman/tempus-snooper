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
