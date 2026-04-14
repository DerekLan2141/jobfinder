import requests
from bs4 import BeautifulSoup
from .base import matches_entry_level

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}

BUILTIN_URL = "https://builtin.com/jobs/dev-engineer/entry-level"


def scrape(max_pages: int = 3) -> list[dict]:
    jobs = []
    for page in range(1, max_pages + 1):
        url = BUILTIN_URL if page == 1 else f"{BUILTIN_URL}?page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f"[builtin] Error fetching page {page}: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("div[data-id]")  # Builtin job cards

        for card in cards:
            title_el = card.select_one("a[data-type='job-title']")
            company_el = card.select_one("a[data-type='company-name']")
            location_el = card.select_one("span[data-type='job-location']")
            snippet_el = card.select_one("div[data-type='job-description']")

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            company = company_el.get_text(strip=True) if company_el else ""
            location = location_el.get_text(strip=True) if location_el else ""
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            href = title_el.get("href", "")
            job_url = f"https://builtin.com{href}" if href.startswith("/") else href

            combined_text = f"{title} {snippet}"
            matched = matches_entry_level(combined_text)

            jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "url": job_url,
                "source": "builtin",
                "description_snippet": snippet[:500],
                "matched_keywords": ", ".join(matched),
                "date_posted": "",
            })

    return jobs
