"""
Handshake scraper.
Uses the public-facing Handshake job search page (no login required for public listings).
Full access requires HANDSHAKE_EMAIL / HANDSHAKE_PASSWORD env vars; without them
only publicly visible postings are returned.
"""
import json
import requests
from bs4 import BeautifulSoup
from .base import matches_entry_level, is_us_location

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://joinhandshake.com/",
}

# Public search endpoint (unauthenticated)
SEARCH_URL = "https://joinhandshake.com/api/jobs/search"

PARAMS_BASE = {
    "per_page": 25,
    "sort_direction": "desc",
    "sort_column": "default",
    "job_type_names[]": "Full-Time",
    "employment_type_names[]": "Entry Level",
    "location": "United States",
}

KEYWORDS = ["software engineer", "software developer", "data analyst", "product manager"]


def scrape(max_pages: int = 3) -> list[dict]:
    jobs = []
    seen_urls: set[str] = set()

    for keyword in KEYWORDS:
        for page in range(1, max_pages + 1):
            params = {**PARAMS_BASE, "page": page, "query": keyword}
            try:
                resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
                if resp.status_code in (401, 403):
                    print("[handshake] Public API requires auth — skipping. Set HANDSHAKE_EMAIL/PASSWORD to enable.")
                    return jobs
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"[handshake] Error for keyword '{keyword}' page {page}: {e}")
                break

            items = data.get("jobs") or data.get("results") or []
            if not items:
                break

            for job in items:
                try:
                    title   = job.get("title", "").strip()
                    company = (job.get("employer") or {}).get("name", "").strip()
                    location = job.get("job_location") or job.get("location") or ""
                    if isinstance(location, dict):
                        location = location.get("name", "")
                    job_id  = job.get("id", "")
                    job_url = f"https://app.joinhandshake.com/stu/jobs/{job_id}" if job_id else ""

                    if not title or not job_url or job_url in seen_urls:
                        continue
                    if not is_us_location(str(location)):
                        continue
                    seen_urls.add(job_url)

                    matched = matches_entry_level(f"{title} entry level new grad")

                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": str(location),
                        "url": job_url,
                        "source": "handshake",
                        "description_snippet": job.get("description", "")[:500],
                        "matched_keywords": ", ".join(matched) if matched else "entry level",
                        "date_posted": job.get("created_at", ""),
                    })
                except Exception as e:
                    print(f"[handshake] Error parsing job: {e}")
                    continue

            if len(items) < 25:
                break  # last page

    print(f"[handshake] Found {len(jobs)} jobs")
    return jobs
