import os
import json
import re
from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from models import db, Job, Profile
from services.adzuna import fetch_jobs

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
    # Migrate existing profile table — add columns introduced in rewrite
    with db.engine.connect() as _conn:
        for _col, _ddl in [
            ("search_queries",   "TEXT"),
            ("experience_level", "VARCHAR(50)"),
            ("summary",          "TEXT"),
            ("education",        "VARCHAR(300)"),
        ]:
            try:
                _conn.execute(db.text(
                    f"ALTER TABLE profile ADD COLUMN IF NOT EXISTS {_col} {_ddl}"
                ))
            except Exception:
                pass
        # Add new job columns introduced in rewrite
        for _col, _ddl in [
            ("salary",             "TEXT"),
            ("source",             "VARCHAR(50)"),
            ("ai_description",     "TEXT"),
            ("ai_salary_estimate", "VARCHAR(200)"),
        ]:
            try:
                _conn.execute(db.text(
                    f"ALTER TABLE job ADD COLUMN IF NOT EXISTS {_col} {_ddl}"
                ))
            except Exception:
                pass
        _conn.commit()


# ── Gemini ─────────────────────────────────────────────────────────────────────

GEMINI_MODEL  = "gemini-2.5-flash"
_gemini_client = None


def gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _gemini_client


def gemini_generate(prompt: str) -> str:
    response = gemini_client().models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

def classify_industry(title: str, snippet: str = "") -> str:
    text = (title + " " + (snippet or "")).lower()
    if any(k in text for k in ["cybersecurity", "security engineer", "security analyst", "infosec"]):
        return "Cybersecurity"
    if any(k in text for k in ["data scientist", "data analyst", "machine learning", "ml engineer", "ai engineer", "data engineer"]):
        return "Data & Analytics"
    if any(k in text for k in ["product manager", "product owner", "associate pm"]):
        return "Product Management"
    if any(k in text for k in ["software engineer", "software developer", "backend", "frontend", "full stack", "devops", "mobile", "web developer"]):
        return "Software Engineering"
    if any(k in text for k in ["designer", "ux", "ui ", "user experience", "graphic design"]):
        return "Design"
    if any(k in text for k in ["financial analyst", "finance", "accounting", "investment", "banking"]):
        return "Finance"
    if any(k in text for k in ["marketing", "seo", "social media", "brand", "copywriter"]):
        return "Marketing"
    if any(k in text for k in ["sales", "business development", "account executive", "customer success"]):
        return "Sales"
    return "Other"


def calc_match_score(job: Job, skills: list[str], titles: list[str]) -> int:
    """Keyword overlap score normalised to 80-99% range."""
    if not skills and not titles:
        return 80
    haystack = " ".join(filter(None, [
        job.title, job.company, job.description_snippet, job.industry
    ])).lower()
    terms = [t.lower() for t in (skills + titles) if t]
    if not terms:
        return 80
    hits = sum(1 for t in terms if t in haystack)
    raw = hits / len(terms)  # 0.0–1.0
    return min(99, 80 + round(raw * 19))


def _active_profile() -> Profile | None:
    return Profile.query.filter_by(id=1).first()


def _load_json(val) -> list:
    if not val:
        return []
    try:
        return json.loads(val)
    except Exception:
        return []


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    # Fresh start on every page load
    Profile.query.filter_by(id=1).delete()
    Job.query.filter(Job.is_saved == False).delete()
    db.session.commit()
    return render_template("index.html")


@app.route("/api/profile")
def get_profile():
    profile = _active_profile()
    if not profile:
        return jsonify(None)
    return jsonify({
        "skills":           _load_json(profile.skills),
        "job_titles":       _load_json(profile.job_titles),
        "search_queries":   _load_json(profile.search_queries),
        "summary":          profile.summary,
        "education":        profile.education,
        "experience_level": profile.experience_level,
    })


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
        text   = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if not text:
            return jsonify({"error": "Could not extract text from PDF."}), 400

        prompt = f"""Analyze this resume and extract key information for job searching. Return ONLY valid JSON — no markdown, no code blocks.

Resume:
{text[:8000]}

Return exactly this JSON:
{{
  "skills": ["list every technical skill, tool, language, and framework mentioned"],
  "job_titles": ["job titles this person has held or is targeting"],
  "experience_level": "entry/junior/mid/senior",
  "education": "highest degree and field of study",
  "summary": "2-3 sentence professional summary of this candidate",
  "search_queries": ["5-8 Adzuna search queries for this resume. IMPORTANT: all queries must target entry-level / new grad roles. Always include terms like 'entry level', 'junior', 'associate', 'new grad', or 'analyst'. Examples: 'entry level python developer', 'junior data analyst SQL', 'associate software engineer React', 'new grad machine learning engineer'"]
}}"""

        data = json.loads(gemini_generate(prompt))

        # Upsert profile (always id=1)
        profile = Profile.query.filter_by(id=1).first()
        if profile is None:
            profile = Profile(id=1)
            db.session.add(profile)

        profile.skills           = json.dumps(data.get("skills", []))
        profile.job_titles       = json.dumps(data.get("job_titles", []))
        profile.search_queries   = json.dumps(data.get("search_queries", []))
        profile.summary          = data.get("summary", "")
        profile.education        = data.get("education", "")
        profile.experience_level = data.get("experience_level", "")
        db.session.commit()

        # Fetch and cache jobs from Adzuna using the profile's queries
        queries = data.get("search_queries", [])
        if queries:
            _refresh_jobs(queries)

        return jsonify({
            "skills":           data.get("skills", []),
            "job_titles":       data.get("job_titles", []),
            "search_queries":   data.get("search_queries", []),
            "summary":          data.get("summary", ""),
            "education":        data.get("education", ""),
            "experience_level": data.get("experience_level", ""),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/resume", methods=["DELETE"])
def clear_resume():
    Profile.query.filter_by(id=1).delete()
    # Clear cached jobs so next upload gets fresh results
    Job.query.filter(Job.is_saved == False).delete()
    db.session.commit()
    return jsonify({"success": True})


def _refresh_jobs(queries: list[str], limit: int = 4, max_pages: int = 1):
    """Fetch jobs from Adzuna and store new ones."""
    results = fetch_jobs(queries[:limit], results_per_page=50, max_pages=max_pages)
    count = 0
    for data in results:
        if Job.query.filter_by(url=data["url"]).first():
            continue
        data["industry"] = classify_industry(data.get("title", ""), data.get("description_snippet", ""))
        data["source"]   = "adzuna"
        job = Job(**{k: v for k, v in data.items() if hasattr(Job, k)})
        db.session.add(job)
        count += 1
    db.session.commit()
    print(f"[refresh] Added {count} new jobs")


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

    profile = _active_profile()
    if profile:
        skills = _load_json(profile.skills)
        titles = _load_json(profile.job_titles)
        scored = [(j, calc_match_score(j, skills, titles)) for j in jobs]
        scored.sort(key=lambda x: x[1], reverse=True)
        return jsonify([j.to_dict(match_score=s) for j, s in scored])

    return jsonify([j.to_dict() for j in jobs])


@app.route("/api/jobs/refresh", methods=["POST"])
def refresh_jobs():
    profile = _active_profile()
    if not profile:
        return jsonify({"error": "Upload a resume first to find matching jobs."}), 400
    queries = _load_json(profile.search_queries)
    if not queries:
        return jsonify({"error": "No search queries found in profile."}), 400
    try:
        _refresh_jobs(queries, limit=len(queries), max_pages=3)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        return jsonify({
            "description":     job.ai_description,
            "salary_estimate": job.ai_salary_estimate or "Not available",
        })

    if not os.getenv("GEMINI_API_KEY"):
        return jsonify({"error": "GEMINI_API_KEY not set"}), 500

    try:
        prompt = f"""Analyze this job posting and respond with ONLY valid JSON — no markdown, no code blocks.

Job Title: {job.title}
Company: {job.company}
Location: {job.location or "Not specified"}
Description: {job.description_snippet or "Not provided"}

Return exactly this JSON:
{{
  "description": "2-3 paragraphs describing the role, responsibilities, and what the company is looking for.",
  "salary_estimate": "Realistic US salary range, e.g. '$75,000 - $95,000/year'."
}}"""

        result = json.loads(gemini_generate(prompt))
        job.ai_description     = result.get("description", "")
        job.ai_salary_estimate = result.get("salary_estimate", "Not available")
        db.session.commit()

        return jsonify({"description": job.ai_description, "salary_estimate": job.ai_salary_estimate})

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


if __name__ == "__main__":
    app.run(debug=True)
