import re

ENTRY_LEVEL_KEYWORDS = [
    "new grad", "new graduate", "recent graduate", "entry level", "entry-level",
    "junior", "0-1 years", "0-2 years", "0-3 years", "1 year of experience",
    "no experience required", "early career", "associate",
]

# US state abbreviations as whole words
_US_STATES = re.compile(
    r'\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|'
    r'MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|'
    r'TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b'
)

_US_KEYWORDS = [
    "united states", "usa", "u.s.a", "u.s.", "remote",
    "new york", "los angeles", "chicago", "houston", "phoenix",
    "san francisco", "seattle", "boston", "austin", "denver",
    "atlanta", "miami", "dallas", "washington", "philadelphia",
    "san diego", "portland", "nashville", "detroit", "minneapolis",
]


def is_us_location(location: str) -> bool:
    """Return True if the location appears to be in the United States."""
    if not location or location.strip() == "":
        return True  # no location = keep (likely remote/unspecified)
    loc = location.strip()
    if any(kw in loc.lower() for kw in _US_KEYWORDS):
        return True
    if _US_STATES.search(loc):
        return True
    return False


def matches_entry_level(text: str) -> list[str]:
    """Return list of matched keywords found in text (case-insensitive)."""
    text_lower = text.lower()
    return [kw for kw in ENTRY_LEVEL_KEYWORDS if kw in text_lower]
