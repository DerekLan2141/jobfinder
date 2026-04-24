"""
Adzuna job API — https://developer.adzuna.com
Fetches jobs using dynamic search queries derived from a resume profile.
"""
import os
import requests

BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"

# Exclude senior/leadership roles from results
_SENIOR_KEYWORDS = [
    "senior", "sr.", "lead ", "staff ", "principal", "manager", "director",
    "vp ", "vice president", "head of", "chief", "president", "architect",
    "cto", "ceo", "cfo",
]


def _is_entry_level(title: str) -> bool:
    t = title.lower()
    return not any(kw in t for kw in _SENIOR_KEYWORDS)


def fetch_jobs(queries: list[str], results_per_page: int = 50, max_pages: int = 1) -> list[dict]:
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
                # Use city name only (last element of area hierarchy)
                location = area[-1] if area else ""
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
                if not _is_entry_level(title):
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
