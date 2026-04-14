"""
LinkedIn scraper using Playwright.
LinkedIn heavily rate-limits and blocks scrapers — this uses public job search
(no login required) but may require occasional CAPTCHA handling.
Note: Playwright is not available in Vercel deployments. Run scrapers locally.
"""
import time
import random
from .base import matches_entry_level

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

SEARCH_QUERIES = [
    "new grad software engineer",
    "entry level software developer",
    "junior software engineer",
]
BASE_URL = "https://www.linkedin.com/jobs/search/?keywords={query}&f_E=1&f_JT=F"


def scrape(max_jobs: int = 30) -> list[dict]:
    if not _PLAYWRIGHT_AVAILABLE:
        print("[linkedin] Playwright not installed — skipping. Install it locally to scrape LinkedIn.")
        return []

    jobs = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        for query in SEARCH_QUERIES:
            if len(jobs) >= max_jobs:
                break

            url = BASE_URL.format(query=query.replace(" ", "%20"))
            try:
                page.goto(url, timeout=30000)
                time.sleep(random.uniform(2, 4))
            except Exception as e:
                print(f"[linkedin] Failed to load {url}: {e}")
                continue

            cards = page.query_selector_all("div.job-search-card")
            for card in cards:
                try:
                    title_el = card.query_selector("h3.base-search-card__title")
                    company_el = card.query_selector("h4.base-search-card__subtitle")
                    location_el = card.query_selector("span.job-search-card__location")
                    link_el = card.query_selector("a.base-card__full-link")
                    date_el = card.query_selector("time")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    location = location_el.inner_text().strip() if location_el else ""
                    job_url = link_el.get_attribute("href") if link_el else ""
                    date_posted = date_el.get_attribute("datetime") if date_el else ""

                    if not title or not job_url or job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)

                    combined_text = f"{title} {query}"
                    matched = matches_entry_level(combined_text)

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

            time.sleep(random.uniform(3, 6))

        browser.close()

    return jobs
