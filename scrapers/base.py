ENTRY_LEVEL_KEYWORDS = [
    "new grad",
    "new graduate",
    "recent graduate",
    "entry level",
    "entry-level",
    "junior",
    "0-1 years",
    "0-2 years",
    "0-3 years",
    "1 year of experience",
    "no experience required",
    "early career",
    "associate",
]


def matches_entry_level(text: str) -> list[str]:
    """Return list of matched keywords found in text (case-insensitive)."""
    text_lower = text.lower()
    return [kw for kw in ENTRY_LEVEL_KEYWORDS if kw in text_lower]
