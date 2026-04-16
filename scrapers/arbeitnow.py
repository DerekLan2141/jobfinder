"""
Arbeitnow job board scraper.
Uses their free public API — no auth, no browser needed.
https://www.arbeitnow.com/api/job-board-api
"""
import requests
from .base import matches_entry_level, is_us_location

API_URL = "https://www.arbeitnow.com/api/job-board-api"


def scrape(max_pages: int = 4) -> list[dict]:
    jobs = []
    seen_urls = set()

    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(
                API_URL,
                params={"page": page},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[arbeitnow] Error on page {page}: {e}")
            break

        items = data.get("data", [])
        if not items:
            break

        for job in items:
            title = job.get("title", "").strip()
            company = job.get("company_name", "").strip()
            location = job.get("location", "")
            if job.get("remote") and not location:
                location = "Remote"
            url = job.get("url", "").strip()
            description = job.get("description", "")
            tags = ", ".join(job.get("tags", []))
            created_at = job.get("created_at", "")

            if not title or not url or url in seen_urls:
                continue
            if not is_us_location(location):
                continue
            seen_urls.add(url)

            combined = f"{title} {description} {tags}"
            matched = matches_entry_level(combined)
            if not matched:
                continue

            jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "source": "arbeitnow",
                "description_snippet": description[:500] if description else "",
                "matched_keywords": ", ".join(matched),
                "date_posted": str(created_at),
            })

    return jobs
