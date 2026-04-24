import os
import json
import re
from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from models import db, Job, Profile
from scrapers import arbeitnow, remoteok, linkedin, handshake, adzuna
from services.embedder import (
    embed_one, embed_batch, cosine_similarity,
    job_text, load_embedding, dump_embedding,
)

load_dotenv()

app = Flask(__name__)

_db_url = os.getenv("POSTGRES_URL", "sqlite:///jobs.db")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
db.init_app(app)

with app.app_context():
    db.create_all()


# ── Gemini helpers ─────────────────────────────────────────────────────────────

def gemini_client():
    from google import genai
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


GEMINI_MODEL = "gemini-2.5-flash"


def gemini_generate(prompt: str) -> str:
    response = gemini_client().models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ── Industry classifier ────────────────────────────────────────────────────────

def classify_industry(title: str, snippet: str = "") -> str:
    text = (title + " " + (snippet or "")).lower()
    if any(k in text for k in ["cybersecurity", "security engineer", "security analyst", "infosec"]):
        return "Cybersecurity"
    if any(k in text for k in ["data scientist", "data analyst", "machine learning", "ml engineer", "ai engineer", "data engineer", "analytics"]):
        return "Data & Analytics"
    if any(k in text for k in ["product manager", "product owner", "product management", "associate pm"]):
        return "Product Management"
    if any(k in text for k in ["software engineer", "software developer", "programmer", "backend", "frontend", "full stack", "fullstack", "devops", "platform engineer", "mobile engineer", "web developer"]):
        return "Software Engineering"
    if any(k in text for k in ["designer", "ux ", "ui ", "user experience", "graphic design", "visual design", "product design"]):
        return "Design"
    if any(k in text for k in ["financial analyst", "finance", "accounting", "investment", "banking", "fintech"]):
        return "Finance"
    if any(k in text for k in ["marketing", "seo", "social media", "brand manager", "copywriter", "demand generation"]):
        return "Marketing"
    if any(k in text for k in ["sales", "business development", "account executive", "account manager", "customer success"]):
        return "Sales"
    if any(k in text for k in ["recruiter", "human resources", "talent acquisition", "people operations"]):
        return "HR & Recruiting"
    if any(k in text for k in ["operations", "project manager", "program manager", "supply chain", "logistics"]):
        return "Operations & Strategy"
    return "Other"


# ── Scraping & embedding ───────────────────────────────────────────────────────

def run_scrapers():
    print("[scheduler] Running scrapers...")
    scrapers_list = [
        ("adzuna",    adzuna.scrape),
        ("arbeitnow", arbeitnow.scrape),
        ("remoteok",  remoteok.scrape),
        ("linkedin",  linkedin.scrape),
        ("handshake", handshake.scrape),
    ]

    new_jobs: list[Job] = []

    for name, scrape_fn in scrapers_list:
        try:
            results = scrape_fn()
            count = 0
            for job_data in results:
                if Job.query.filter_by(url=job_data["url"]).first():
                    continue
                job_data["industry"] = classify_industry(
                    job_data.get("title", ""),
                    job_data.get("description_snippet", ""),
                )
                job = Job(**{k: v for k, v in job_data.items() if hasattr(Job, k)})
                db.session.add(job)
                new_jobs.append(job)
                count += 1
            db.session.commit()
            print(f"[{name}] Added {count} new jobs")
        except Exception as e:
            print(f"[{name}] Scraper failed: {e}")

    # Generate embeddings for all new jobs in one batch
    _embed_jobs(new_jobs)


def _embed_jobs(jobs: list[Job]):
    """Generate and store embeddings for a list of Job rows (in-place batch)."""
    if not jobs or not os.getenv("GEMINI_API_KEY"):
        return
    try:
        texts = [job_text(j) for j in jobs]
        vecs  = embed_batch(texts)
        for job, vec in zip(jobs, vecs):
            job.embedding = dump_embedding(vec)
        db.session.commit()
        print(f"[embedder] Embedded {len(jobs)} jobs")
    except Exception as e:
        print(f"[embedder] Batch embedding failed: {e}")


def _get_active_profile() -> Profile | None:
    return Profile.query.filter_by(id=1).first()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/jobs")
def get_jobs():
    saved_only = request.args.get("saved") == "true"
    search     = request.args.get("search", "").strip()
    location   = request.args.get("location", "").strip()
    industry   = request.args.get("industry", "").strip()

    query = Job.query.filter_by(is_dismissed=False)
    if saved_only:
        query = query.filter_by(is_saved=True)
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            Job.title.ilike(like),
            Job.company.ilike(like),
            Job.description_snippet.ilike(like),
        ))
    if location:
        locs = [l.strip() for l in location.split(",") if l.strip()]
        if locs:
            query = query.filter(db.or_(*[Job.location.ilike(f"%{l}%") for l in locs]))
    if industry:
        query = query.filter_by(industry=industry)

    jobs = query.order_by(Job.date_scraped.desc()).all()

    # Vector ranking — apply when an active profile exists
    profile = _get_active_profile()
    if profile:
        profile_vec = load_embedding(profile.embedding)
        if profile_vec:
            # Embed any jobs that are missing embeddings (up to 50 at a time to stay fast)
            unembedded = [j for j in jobs if not j.embedding][:50]
            if unembedded:
                _embed_jobs(unembedded)

            scored: list[tuple[Job, int]] = []
            for j in jobs:
                vec = load_embedding(j.embedding)
                score = round(cosine_similarity(profile_vec, vec) * 100) if vec else 0
                scored.append((j, score))

            scored.sort(key=lambda x: x[1], reverse=True)
            return jsonify([j.to_dict(match_score=s) for j, s in scored])

    return jsonify([j.to_dict() for j in jobs])


@app.route("/api/filters")
def get_filters():
    industry_rows = (
        db.session.query(Job.industry)
        .filter(Job.industry.isnot(None), Job.industry != "", Job.industry != "Other")
        .distinct().all()
    )
    location_rows = (
        db.session.query(Job.location)
        .filter(Job.location.isnot(None), Job.location != "")
        .distinct().all()
    )
    return jsonify({
        "industries": sorted([r[0] for r in industry_rows]),
        "locations":  sorted(set(r[0].strip() for r in location_rows if r[0])),
    })


@app.route("/api/jobs/<int:job_id>/analyze", methods=["POST"])
def analyze_job(job_id):
    job = Job.query.get_or_404(job_id)

    if job.ai_description:
        return jsonify({"description": job.ai_description, "salary_estimate": job.ai_salary_estimate or "Not available"})

    if not os.getenv("GEMINI_API_KEY"):
        return jsonify({"error": "GEMINI_API_KEY not set"}), 500

    try:
        prompt = f"""You are a job listing analyst. Analyze this job posting and respond with ONLY valid JSON — no markdown, no code blocks.

Job Title: {job.title}
Company: {job.company}
Location: {job.location or "Not specified"}
Source: {job.source}
Description: {job.description_snippet or "Not provided"}

Return exactly this JSON structure:
{{
  "description": "Write 2-3 paragraphs describing what this role involves, key responsibilities, and what the company is looking for.",
  "salary_estimate": "Realistic US salary range (e.g. '$75,000 - $95,000/year')."
}}"""

        data = json.loads(gemini_generate(prompt))
        job.ai_description     = data.get("description", "")
        job.ai_salary_estimate = data.get("salary_estimate", "Not available")
        if not job.industry:
            job.industry = classify_industry(job.title, job.description_snippet or "")
        db.session.commit()

        return jsonify({"description": job.ai_description, "salary_estimate": job.ai_salary_estimate})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/resume/upload", methods=["POST"])
def upload_resume():
    if "resume" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["resume"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported"}), 400
    if not os.getenv("GEMINI_API_KEY"):
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500

    try:
        import pypdf, io as _io
        reader = pypdf.PdfReader(_io.BytesIO(file.read()))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if not text:
            return jsonify({"error": "Could not extract text — make sure the PDF is not a scanned image."}), 400

        # Step 1: extract structured profile + search_text via Gemini
        prompt = f"""Analyze this resume for job matching. Return ONLY valid JSON — no markdown, no code blocks.

Resume:
{text[:8000]}

Return exactly this JSON structure:
{{
  "skills": ["every technical skill, tool, language, and framework mentioned"],
  "job_titles": ["job titles held or targeted"],
  "industries": ["relevant industries"],
  "years_experience": 0,
  "education": "highest degree and field",
  "summary": "2-3 sentence professional summary",
  "search_text": "A 3-5 sentence description of this candidate written to maximise semantic similarity with relevant job postings. Include target roles, seniority level, key technical skills, domain experience, and career goals."
}}"""

        profile_data = json.loads(gemini_generate(prompt))
        search_text  = profile_data.get("search_text", profile_data.get("summary", text[:500]))

        # Step 2: embed the search_text
        profile_vec = embed_one(search_text)

        # Step 3: upsert into Profile table (always id=1)
        profile = Profile.query.filter_by(id=1).first()
        if profile is None:
            profile = Profile(id=1)
            db.session.add(profile)

        profile.skills      = json.dumps(profile_data.get("skills", []))
        profile.job_titles  = json.dumps(profile_data.get("job_titles", []))
        profile.search_text = search_text
        profile.embedding   = dump_embedding(profile_vec)
        db.session.commit()

        # Return profile data to client (skills shown in UI, other fields for display)
        return jsonify({
            "skills":           profile_data.get("skills", []),
            "job_titles":       profile_data.get("job_titles", []),
            "industries":       profile_data.get("industries", []),
            "years_experience": profile_data.get("years_experience", 0),
            "education":        profile_data.get("education", ""),
            "summary":          profile_data.get("summary", ""),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/resume", methods=["DELETE"])
def clear_resume():
    Profile.query.filter_by(id=1).delete()
    db.session.commit()
    return jsonify({"success": True})


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


@app.route("/api/scrape", methods=["POST", "GET"])
def trigger_scrape():
    if request.method == "GET":
        cron_secret = os.getenv("CRON_SECRET")
        if cron_secret and request.headers.get("Authorization", "") != f"Bearer {cron_secret}":
            return jsonify({"error": "Unauthorized"}), 401
    run_scrapers()
    return jsonify({"success": True})


if __name__ == "__main__":
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=lambda: app.app_context().__enter__() or run_scrapers(),
            trigger="interval", hours=6, id="scrape_jobs",
        )
        scheduler.start()
    except Exception as e:
        print(f"[scheduler] Could not start: {e}")

    app.run(debug=True, use_reloader=False)
