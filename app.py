import os
import json
import re
from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from models import db, Job
from scrapers import arbeitnow, remoteok, linkedin, handshake

load_dotenv()

app = Flask(__name__)

# Support both local SQLite and Vercel Postgres (POSTGRES_URL env var)
_db_url = os.getenv("POSTGRES_URL", "sqlite:///jobs.db")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB upload limit
db.init_app(app)

# Create tables on startup — works for both local and Vercel cold starts
with app.app_context():
    db.create_all()


def classify_industry(title: str, snippet: str = "") -> str:
    text = (title + " " + (snippet or "")).lower()
    if any(k in text for k in ["cybersecurity", "security engineer", "security analyst", "information security", "infosec", "penetration"]):
        return "Cybersecurity"
    if any(k in text for k in ["data scientist", "data analyst", "machine learning", "ml engineer", "ai engineer", "data engineer", "analytics engineer"]):
        return "Data & Analytics"
    if any(k in text for k in ["product manager", "product owner", "product management", "associate pm", " apm "]):
        return "Product Management"
    if any(k in text for k in ["software engineer", "software developer", "programmer", "backend", "frontend", "full stack", "fullstack", "devops", "sre ", "platform engineer", "mobile engineer", "ios ", "android ", "web developer"]):
        return "Software Engineering"
    if any(k in text for k in ["designer", "ux ", "ui ", "user experience", "user interface", "graphic design", "visual design", "product design"]):
        return "Design"
    if any(k in text for k in ["financial analyst", "finance", "accounting", "investment", "banking", "fintech", "actuar"]):
        return "Finance"
    if any(k in text for k in ["marketing", "content writer", "seo", "social media", "brand manager", "growth hacker", "copywriter", "demand generation"]):
        return "Marketing"
    if any(k in text for k in ["sales", "business development", "account executive", "account manager", "customer success"]):
        return "Sales"
    if any(k in text for k in ["recruiter", "human resources", "hr ", "talent acquisition", "people operations", "people partner"]):
        return "HR & Recruiting"
    if any(k in text for k in ["operations", "project manager", "program manager", "supply chain", "logistics", "strategy analyst"]):
        return "Operations & Strategy"
    if any(k in text for k in ["research scientist", "biologist", "chemist", "physicist", "research engineer", "research analyst"]):
        return "Research & Science"
    return "Other"


def run_scrapers():
    """Run all scrapers and save new jobs to the database."""
    print("[scheduler] Running scrapers...")
    scrapers = [
        ("arbeitnow", arbeitnow.scrape),
        ("remoteok", remoteok.scrape),
        ("linkedin", linkedin.scrape),
        ("handshake", handshake.scrape),
    ]
    for name, scrape_fn in scrapers:
        try:
            results = scrape_fn()
            new_count = 0
            for job_data in results:
                if not Job.query.filter_by(url=job_data["url"]).first():
                    job_data["industry"] = classify_industry(
                        job_data.get("title", ""),
                        job_data.get("description_snippet", ""),
                    )
                    job = Job(**job_data)
                    db.session.add(job)
                    new_count += 1
            db.session.commit()
            print(f"[{name}] Added {new_count} new jobs")
        except Exception as e:
            print(f"[{name}] Scraper failed: {e}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jobs")
def get_jobs():
    source = request.args.get("source", "")
    saved_only = request.args.get("saved") == "true"
    show_dismissed = request.args.get("dismissed") == "true"
    search = request.args.get("search", "").strip()
    location = request.args.get("location", "").strip()
    industry = request.args.get("industry", "").strip()

    query = Job.query.filter_by(is_dismissed=False) if not show_dismissed else Job.query
    if source:
        query = query.filter_by(source=source)
    if saved_only:
        query = query.filter_by(is_saved=True)
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                Job.title.ilike(like),
                Job.company.ilike(like),
                Job.description_snippet.ilike(like),
            )
        )
    if location:
        query = query.filter(Job.location.ilike(f"%{location}%"))
    if industry:
        query = query.filter_by(industry=industry)

    jobs = query.order_by(Job.date_scraped.desc()).all()
    return jsonify([j.to_dict() for j in jobs])


@app.route("/api/filters")
def get_filters():
    rows = (
        db.session.query(Job.industry)
        .filter(Job.industry.isnot(None), Job.industry != "", Job.industry != "Other")
        .distinct()
        .all()
    )
    return jsonify({"industries": sorted([r[0] for r in rows])})


@app.route("/api/jobs/<int:job_id>/analyze", methods=["POST"])
def analyze_job(job_id):
    job = Job.query.get_or_404(job_id)

    if job.ai_description:
        return jsonify({
            "description": job.ai_description,
            "salary_estimate": job.ai_salary_estimate or "Not available",
        })

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY not set in .env"}), 500

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = f"""You are a job listing analyst. Analyze this job posting and respond with ONLY valid JSON — no markdown, no code blocks.

Job Title: {job.title}
Company: {job.company}
Location: {job.location or "Not specified"}
Source: {job.source}
Description: {job.description_snippet or "Not provided"}

Return exactly this JSON structure:
{{
  "description": "Write 2-3 paragraphs describing what this role involves, key responsibilities, and what the company is looking for in a candidate.",
  "salary_estimate": "Realistic salary range for this role and location (e.g. '$75,000 - $95,000/year'). Use US averages if location is unspecified or remote."
}}"""

        response = model.generate_content(prompt)
        text = response.text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        data = json.loads(text.strip())

        job.ai_description = data.get("description", "")
        job.ai_salary_estimate = data.get("salary_estimate", "Not available")

        if not job.industry:
            job.industry = classify_industry(job.title, job.description_snippet or "")

        db.session.commit()

        return jsonify({
            "description": job.ai_description,
            "salary_estimate": job.ai_salary_estimate,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/jobs/<int:job_id>/save", methods=["POST"])
def toggle_save(job_id):
    job = Job.query.get_or_404(job_id)
    job.is_saved = not job.is_saved
    db.session.commit()
    return jsonify({"is_saved": job.is_saved})


@app.route("/api/jobs/<int:job_id>/dismiss", methods=["POST"])
def dismiss_job(job_id):
    job = Job.query.get_or_404(job_id)
    job.is_dismissed = True
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/jobs/<int:job_id>/notes", methods=["POST"])
def update_notes(job_id):
    job = Job.query.get_or_404(job_id)
    job.notes = request.json.get("notes", "")
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/resume/upload", methods=["POST"])
def upload_resume():
    if "resume" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["resume"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500

    try:
        import pypdf
        import io as _io

        pdf_bytes = file.read()
        reader = pypdf.PdfReader(_io.BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()

        if not text:
            return jsonify({"error": "Could not extract text from this PDF. Make sure it is not a scanned image."}), 400

        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = f"""Analyze this resume and extract key information. Return ONLY valid JSON — no markdown, no code blocks.

Resume:
{text[:8000]}

Return exactly this JSON structure:
{{
  "skills": ["list every technical skill, tool, language, and framework mentioned"],
  "job_titles": ["job titles held or clearly targeted by this candidate"],
  "industries": ["relevant industries based on experience"],
  "years_experience": 0,
  "education": "highest degree and field of study",
  "summary": "2-3 sentence professional summary of this candidate"
}}"""

        response = model.generate_content(prompt)
        result = response.text.strip()
        result = re.sub(r"^```(?:json)?\s*", "", result)
        result = re.sub(r"\s*```$", "", result)

        profile = json.loads(result.strip())
        return jsonify(profile)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scrape", methods=["POST", "GET"])
def trigger_scrape():
    """Trigger a scrape run. GET is used by Vercel Cron Jobs."""
    if request.method == "GET":
        cron_secret = os.getenv("CRON_SECRET")
        if cron_secret:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {cron_secret}":
                return jsonify({"error": "Unauthorized"}), 401
    run_scrapers()
    return jsonify({"success": True})


if __name__ == "__main__":
    scheduler_started = False
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=lambda: app.app_context().__enter__() or run_scrapers(),
            trigger="interval",
            hours=6,
            id="scrape_jobs",
        )
        scheduler.start()
        scheduler_started = True
    except Exception as e:
        print(f"[scheduler] Could not start: {e}")

    app.run(debug=True, use_reloader=False)
