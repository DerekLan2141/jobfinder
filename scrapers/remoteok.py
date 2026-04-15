"""
RemoteOK job board scraper.
Uses their free public API — no auth, no browser needed.
https://remoteok.com/api
"""
import requests
from .base import matches_entry_level

TAGS = ["junior", "entry-level", "new-grad"]
BASE_URL = "https://remoteok.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JobFinder/1.0)"}


def scrape() -> list[dict]:
    jobs = []
    seen_urls = set()

    for tag in TAGS:
        try:
            resp = requests.get(
                BASE_URL,
                params={"tag": tag},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # First element is legal metadata, skip it
            if isinstance(data, list) and data:
                data = data[1:]
        except Exception as e:
            print(f"[remoteok] Error for tag '{tag}': {e}")
            continue

        for job in data:
            title = job.get("position", "").strip()
            company = job.get("company", "").strip()
            location = job.get("location", "Remote") or "Remote"
            url = job.get("url", "").strip()
            description = job.get("description", "")
            tags = ", ".join(job.get("tags", []))

            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)

            combined = f"{title} {tags} {description}"
            matched = matches_entry_level(combined)

            jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "source": "remoteok",
                "description_snippet": description[:500] if description else "",
                "matched_keywords": ", ".join(matched) if matched else tag,
                "date_posted": "",
            })

    return jobs
