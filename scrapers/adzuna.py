"""
Adzuna job board scraper.
Free API — register at https://developer.adzuna.com to get ADZUNA_APP_ID and ADZUNA_APP_KEY.
"""
import os
import requests
from .base import matches_entry_level, is_us_location

BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"

SEARCH_QUERIES = [
    "entry level software engineer",
    "junior software developer",
    "new grad software engineer",
    "associate software engineer",
    "entry level data scientist",
    "junior data analyst",
    "entry level frontend developer",
    "entry level backend engineer",
    "junior devops engineer",
    "entry level machine learning",
]


def scrape(results_per_page: int = 20, max_pages: int = 2) -> list[dict]:
    app_id  = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        print("[adzuna] ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipping")
        return []

    jobs: list[dict] = []
    seen_urls: set[str] = set()

    for query in SEARCH_QUERIES:
        for page in range(1, max_pages + 1):
            try:
                resp = requests.get(
                    BASE_URL.format(page=page),
                    params={
                        "app_id": app_id,
                        "app_key": app_key,
                        "what": query,
                        "where": "united states",
                        "results_per_page": results_per_page,
                        "content-type": "application/json",
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

                loc_data = job.get("location") or {}
                area = loc_data.get("area", [])
                location = ", ".join(area[-2:]) if len(area) >= 2 else ", ".join(area)

                url         = job.get("redirect_url", "").strip()
                description = job.get("description", "")
                date_posted = (job.get("created") or "")[:10]

                salary_min = job.get("salary_min")
                salary_max = job.get("salary_max")
                if salary_min and salary_max:
                    salary_str = f"${int(salary_min):,} – ${int(salary_max):,}/year"
                elif salary_min:
                    salary_str = f"${int(salary_min):,}+/year"
                else:
                    salary_str = None

                if not title or not url or url in seen_urls:
                    continue
                if not is_us_location(location):
                    continue
                seen_urls.add(url)

                matched = matches_entry_level(f"{title} {description}")

                jobs.append({
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": url,
                    "source": "adzuna",
                    "description_snippet": description[:500],
                    "matched_keywords": ", ".join(matched) if matched else "entry level",
                    "date_posted": date_posted,
                    "ai_salary_estimate": salary_str,
                })

    print(f"[adzuna] Found {len(jobs)} jobs")
    return jobs
