"""
Adzuna job API — https://developer.adzuna.com
Fetches jobs using dynamic search queries derived from a resume profile.
"""
import os
import re
import requests

BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"

ENTRY_LEVEL_KEYWORDS = [
    "entry level", "entry-level", "new grad", "new graduate", "recent graduate",
    "junior", "associate", "analyst", "0-1 year", "0-2 year", "0-3 year",
    "no experience", "early career", "internship", "intern", "trainee",
    "graduate program", "rotational program",
]


def _is_entry_level(title: str, description: str) -> bool:
    text = (title + " " + description).lower()
    return any(kw in text for kw in ENTRY_LEVEL_KEYWORDS)


_US_STATES = re.compile(
    r'\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|'
    r'MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|'
    r'TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b'
)
_US_KEYWORDS = [
    "united states", "usa", "u.s.", "remote",
    "new york", "los angeles", "chicago", "houston", "phoenix",
    "san francisco", "seattle", "boston", "austin", "denver",
    "atlanta", "miami", "dallas", "washington", "philadelphia",
    "san diego", "portland", "nashville", "minneapolis",
]


def _is_us(location: str) -> bool:
    if not location:
        return True
    loc = location.lower()
    return any(kw in loc for kw in _US_KEYWORDS) or bool(_US_STATES.search(location))


def fetch_jobs(queries: list[str], results_per_page: int = 20, max_pages: int = 2) -> list[dict]:
    """Fetch jobs from Adzuna for a list of search queries."""
    app_id  = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("[adzuna] ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
        return []

    jobs: list[dict] = []
    seen: set[str]   = set()

    for query in queries:
        for page in range(1, max_pages + 1):
            try:
                resp = requests.get(
                    BASE_URL.format(page=page),
                    params={
                        "app_id":           app_id,
                        "app_key":          app_key,
                        "what":             query,
                        "where":            "united states",
                        "results_per_page": results_per_page,
                        "content-type":     "application/json",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[adzuna] Error for '{query}' page {page}: {e}")
                break

            results = data.get("results", [])
            if not results:
                break

            for job in results:
                title   = job.get("title", "").strip()
                company = (job.get("company") or {}).get("display_name", "").strip()
                area    = (job.get("location") or {}).get("area", [])
                location = ", ".join(area[-2:]) if len(area) >= 2 else ", ".join(area)
                url         = job.get("redirect_url", "").strip()
                description = job.get("description", "")
                date_posted = (job.get("created") or "")[:10]

                s_min = job.get("salary_min")
                s_max = job.get("salary_max")
                if s_min and s_max:
                    salary = f"${int(s_min):,} – ${int(s_max):,}/year"
                elif s_min:
                    salary = f"${int(s_min):,}+/year"
                else:
                    salary = None

                if not title or not url or url in seen:
                    continue
                if not _is_us(location):
                    continue
                if not _is_entry_level(title, description):
                    continue
                seen.add(url)

                jobs.append({
                    "title":               title,
                    "company":             company,
                    "location":            location,
                    "url":                 url,
                    "description_snippet": description[:500],
                    "date_posted":         date_posted,
                    "salary":              salary,
                })

    print(f"[adzuna] Fetched {len(jobs)} jobs for {len(queries)} queries")
    return jobs
