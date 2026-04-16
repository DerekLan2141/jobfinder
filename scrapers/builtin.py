"""
Builtin.com scraper.
Extracts job data from the __NEXT_DATA__ JSON embedded in the page.
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
    )
}

SEARCH_URLS = [
    "https://builtin.com/jobs/entry-level?country=United+States",
    "https://builtin.com/jobs/dev-engineer/entry-level?country=United+States",
]


def _extract_next_data(html: str) -> list[dict]:
    """Pull job listings from Next.js embedded JSON."""
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return []
    try:
        data = json.loads(script.string)
        # Navigate to jobs array — path varies by page type
        page_props = data.get("props", {}).get("pageProps", {})
        # Try common paths
        for key in ("jobs", "initialJobs", "jobListings"):
            if key in page_props:
                return page_props[key]
        # Sometimes nested under 'dehydratedState'
        state = page_props.get("dehydratedState", {})
        queries = state.get("queries", [])
        for q in queries:
            result = q.get("state", {}).get("data", {})
            if isinstance(result, dict):
                for key in ("jobs", "data"):
                    if key in result and isinstance(result[key], list):
                        return result[key]
    except Exception as e:
        print(f"[builtin] JSON parse error: {e}")
    return []


def _parse_job(job: dict) -> dict | None:
    """Convert a raw job dict from Next.js data to our format."""
    try:
        title   = job.get("title", "").strip()
        company = (job.get("company") or {}).get("name", "").strip()
        location_parts = []
        if job.get("city"):
            location_parts.append(job["city"])
        if job.get("state"):
            location_parts.append(job["state"])
        if job.get("country"):
            location_parts.append(job["country"])
        location = ", ".join(location_parts) or job.get("location", "")
        slug    = job.get("slug", "")
        job_url = f"https://builtin.com/job/{slug}" if slug else job.get("url", "")
        snippet = job.get("description", "")[:500]

        if not title or not job_url:
            return None
        if not is_us_location(location):
            return None

        matched = matches_entry_level(f"{title} {snippet}")

        return {
            "title": title,
            "company": company,
            "location": location,
            "url": job_url,
            "source": "builtin",
            "description_snippet": snippet,
            "matched_keywords": ", ".join(matched),
            "date_posted": job.get("postedDate", ""),
        }
    except Exception:
        return None


def scrape(max_pages: int = 3) -> list[dict]:
    jobs = []
    seen_urls: set[str] = set()

    for base_url in SEARCH_URLS:
        for page in range(1, max_pages + 1):
            url = base_url if page == 1 else f"{base_url}&page={page}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
            except Exception as e:
                print(f"[builtin] Error fetching {url}: {e}")
                break

            raw_jobs = _extract_next_data(resp.text)
            if not raw_jobs:
                # Fallback: try plain HTML selectors
                soup = BeautifulSoup(resp.text, "lxml")
                for card in soup.select("article[data-id], li[data-id]"):
                    title_el   = card.select_one("[data-type='job-title'], h2 a, h3 a")
                    company_el = card.select_one("[data-type='company-name']")
                    loc_el     = card.select_one("[data-type='job-location'], [class*='location']")
                    if not title_el:
                        continue
                    title    = title_el.get_text(strip=True)
                    company  = company_el.get_text(strip=True) if company_el else ""
                    location = loc_el.get_text(strip=True) if loc_el else ""
                    href     = title_el.get("href", "")
                    job_url  = f"https://builtin.com{href}" if href.startswith("/") else href
                    if not job_url or job_url in seen_urls:
                        continue
                    if not is_us_location(location):
                        continue
                    seen_urls.add(job_url)
                    matched = matches_entry_level(title)
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "url": job_url,
                        "source": "builtin",
                        "description_snippet": "",
                        "matched_keywords": ", ".join(matched),
                        "date_posted": "",
                    })
                break  # don't paginate if Next.js data not found

            for raw in raw_jobs:
                parsed = _parse_job(raw)
                if parsed and parsed["url"] not in seen_urls:
                    seen_urls.add(parsed["url"])
                    jobs.append(parsed)

            if len(raw_jobs) < 20:
                break  # last page

    print(f"[builtin] Found {len(jobs)} jobs")
    return jobs
