"""
Handshake scraper.
Note: Handshake requires a student/alumni login for full access.
This scraper attempts to use the public-facing job board where available.
For full access, set HANDSHAKE_EMAIL and HANDSHAKE_PASSWORD in your .env file.
"""
import time
import os
from playwright.sync_api import sync_playwright
from .base import matches_entry_level

PUBLIC_SEARCH_URL = (
    "https://app.joinhandshake.com/stu/postings?"
    "page=1&per_page=25&sort_direction=desc&sort_column=default"
    "&job_type_names[]=Full-Time&employment_type_names[]=Entry+Level"
)


def scrape(max_jobs: int = 30) -> list[dict]:
    jobs = []
    email = os.getenv("HANDSHAKE_EMAIL", "")
    password = os.getenv("HANDSHAKE_PASSWORD", "")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto("https://app.joinhandshake.com/login", timeout=30000)
            time.sleep(2)

            if email and password:
                page.fill("input[name='email']", email)
                page.fill("input[name='password']", password)
                page.click("button[type='submit']")
                time.sleep(3)

            page.goto(PUBLIC_SEARCH_URL, timeout=30000)
            time.sleep(3)

            cards = page.query_selector_all("div[data-hook='jobs-card']")
            for card in cards[:max_jobs]:
                try:
                    title_el = card.query_selector("a[data-hook='jobs-card-title']")
                    company_el = card.query_selector("span[data-hook='employer-name']")
                    location_el = card.query_selector("span[data-hook='job-location']")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    location = location_el.inner_text().strip() if location_el else ""
                    href = title_el.get_attribute("href") if title_el else ""
                    job_url = f"https://app.joinhandshake.com{href}" if href else ""

                    if not title or not job_url:
                        continue

                    matched = matches_entry_level(f"{title} entry level new grad")

                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": location,
                        "url": job_url,
                        "source": "handshake",
                        "description_snippet": "",
                        "matched_keywords": ", ".join(matched) if matched else "entry level",
                        "date_posted": "",
                    })
                except Exception as e:
                    print(f"[handshake] Error parsing card: {e}")
                    continue

        except Exception as e:
            print(f"[handshake] Scrape failed: {e}")
        finally:
            browser.close()

    return jobs
