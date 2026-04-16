"""
LinkedIn scraper using the public guest jobs API (no login, no Playwright).
https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
"""
import requests
from bs4 import BeautifulSoup
from .base import matches_entry_level, is_us_location

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}

GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

SEARCH_QUERIES = [
    "entry level software engineer",
    "new grad software engineer",
    "junior software developer",
    "associate software engineer",
]


def scrape(max_per_query: int = 25) -> list[dict]:
    jobs = []
    seen_urls = set()

    for query in SEARCH_QUERIES:
        for start in [0, 25]:
            try:
                resp = requests.get(
                    GUEST_API,
                    params={
                        "keywords": query,
                        "location": "United States",
                        "f_E": "2",   # Entry Level
                        "f_JT": "F",  # Full-time
                        "start": start,
                        "count": 25,
                    },
                    headers=HEADERS,
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as e:
                print(f"[linkedin] Error for query '{query}' start={start}: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("div.base-card")

            if not cards:
                break

            for card in cards:
                try:
                    title_el   = card.select_one("h3.base-search-card__title")
                    company_el = card.select_one("h4.base-search-card__subtitle")
                    loc_el     = card.select_one("span.job-search-card__location")
                    link_el    = card.select_one("a.base-card__full-link")
                    date_el    = card.select_one("time")

                    title    = title_el.get_text(strip=True)   if title_el   else ""
                    company  = company_el.get_text(strip=True) if company_el else ""
                    location = loc_el.get_text(strip=True)     if loc_el     else ""
                    job_url  = link_el.get("href", "").split("?")[0] if link_el else ""
                    date_posted = date_el.get("datetime", "") if date_el else ""

                    if not title or not job_url or job_url in seen_urls:
                        continue
                    if not is_us_location(location):
                        continue
                    seen_urls.add(job_url)

                    matched = matches_entry_level(f"{title} {query}")

                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "url": job_url,
                        "source": "linkedin",
                        "description_snippet": "",
                        "matched_keywords": ", ".join(matched) if matched else "entry level",
                        "date_posted": date_posted,
                    })
                except Exception as e:
                    print(f"[linkedin] Error parsing card: {e}")
                    continue

    print(f"[linkedin] Found {len(jobs)} jobs")
    return jobs
