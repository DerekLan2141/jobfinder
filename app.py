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
            ("resume_text",      "TEXT"),
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
            ("cv_guide",           "TEXT"),
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
        profile.resume_text      = text[:6000]
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


def _refresh_jobs(queries: list[str], limit: int = 6, max_pages: int = 2):
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
    visa_only  = request.args.get("visa") == "true"
    search     = request.args.get("search", "").strip()
    location   = request.args.get("location", "").strip()
    industry   = request.args.get("industry", "").strip()

    query = Job.query.filter_by(is_dismissed=False)
    if saved_only:
        query = query.filter_by(is_saved=True)
    if visa_only:
        visa_terms = ["visa sponsor", "h1b", "h-1b", "sponsorship", "will sponsor", "visa support", "work authorization"]
        query = query.filter(db.or_(*[Job.description_snippet.ilike(f"%{t}%") for t in visa_terms]))
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


@app.route("/api/jobs/<int:job_id>/cv-guide", methods=["POST"])
def cv_guide(job_id):
    job = Job.query.get_or_404(job_id)

    if job.cv_guide:
        return jsonify({"suggestions": json.loads(job.cv_guide)})

    profile = _active_profile()
    if not profile:
        return jsonify({"error": "Upload a resume first"}), 400
    if not os.getenv("GEMINI_API_KEY"):
        return jsonify({"error": "GEMINI_API_KEY not set"}), 500

    try:
        skills = _load_json(profile.skills)
        titles = _load_json(profile.job_titles)

        resume_excerpt = (profile.resume_text or "")[:3000]

        prompt = f"""You are a career coach doing a detailed resume review for a specific job application.
Read the actual resume text carefully and give highly specific, personalized suggestions.
Reference real content from the resume — specific projects, skills, experiences, or gaps you notice.

ACTUAL RESUME:
{resume_excerpt}

TARGET JOB:
Title: {job.title}
Company: {job.company}
Location: {job.location or "Not specified"}
Description: {job.description_snippet or "Not provided"}

Give 5-6 specific suggestions. Each must:
- Reference something ACTUALLY in the resume (a real skill, project, or experience)
- Explain exactly what to change, add, or reframe for THIS job
- Be actionable, not generic

Return ONLY valid JSON:
{{
  "suggestions": [
    {{
      "tip": "short actionable title referencing specific resume content",
      "detail": "exactly what to change and why it helps for this specific role"
    }}
  ]
}}"""

        result = json.loads(gemini_generate(prompt))
        suggestions = result.get("suggestions", [])
        job.cv_guide = json.dumps(suggestions)
        db.session.commit()

        return jsonify({"suggestions": suggestions})

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


@app.route("/api/jobs/<int:job_id>/similar")
def similar_jobs(job_id):
    job = Job.query.get_or_404(job_id)
    stop = {"and","the","for","with","this","that","are","you","will","have","from","our","your"}
    title_terms = set(
        w for w in re.sub(r"[^\w\s]", "", job.title.lower()).split()
        if len(w) > 2 and w not in stop
    )
    candidates = Job.query.filter(Job.id != job_id, Job.is_dismissed == False).all()
    scored = []
    for c in candidates:
        score = 0
        if job.industry and c.industry == job.industry:
            score += 6
        c_words = set(re.sub(r"[^\w\s]", "", c.title.lower()).split())
        score += len(title_terms & c_words) * 3
        c_desc = (c.description_snippet or "").lower()
        score += sum(1 for t in title_terms if t in c_desc)
        if score > 0:
            scored.append((c, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return jsonify([c.to_dict() for c, _ in scored[:6]])


@app.route("/api/resume/text")
def resume_text():
    profile = _active_profile()
    if not profile or not profile.resume_text:
        return jsonify({"error": "No resume loaded"}), 404
    return jsonify({"text": profile.resume_text})


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/dashboard/salary-prediction")
def salary_prediction():
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import LabelEncoder
    from sklearn.model_selection import cross_val_score
    import numpy as np

    jobs = Job.query.filter_by(is_dismissed=False).all()
    num_re = re.compile(r'\$([\d,]+)')

    BUCKETS = ["<$50k", "$50–75k", "$75–100k", "$100–130k", "$130k+"]

    def bucket(mid):
        if mid < 50000:  return "<$50k"
        if mid < 75000:  return "$50–75k"
        if mid < 100000: return "$75–100k"
        if mid < 130000: return "$100–130k"
        return "$130k+"

    labeled, unlabeled = [], []
    for job in jobs:
        if job.salary:
            nums = [int(n.replace(",","")) for n in num_re.findall(job.salary)]
            if nums:
                labeled.append({"job": job, "mid": sum(nums)/len(nums), "bucket": bucket(sum(nums)/len(nums))})
                continue
        unlabeled.append(job)

    if len(labeled) < 6:
        return jsonify({"error": f"Only {len(labeled)} jobs have salary data — need at least 6 to train. Try Refresh Jobs."}), 400

    all_inds = sorted(set(j["job"].industry or "Other" for j in labeled))
    all_locs = sorted(set(j["job"].location or "Unknown" for j in labeled))

    def featurize(job):
        return (
            [1 if (job.industry or "Other") == i else 0 for i in all_inds] +
            [1 if (job.location or "Unknown") == l else 0 for l in all_locs]
        )

    X = [featurize(j["job"]) for j in labeled]
    y = [j["bucket"] for j in labeled]

    le  = LabelEncoder()
    y_e = le.fit_transform(y)
    clf = RandomForestClassifier(n_estimators=100, random_state=42)

    cv  = min(5, len(labeled) // 2)
    acc = round(float(cross_val_score(clf, X, y_e, cv=cv, scoring="accuracy").mean()), 3) if cv >= 2 else None
    clf.fit(X, y_e)

    predictions = []
    for job in unlabeled[:30]:
        pred = le.inverse_transform(clf.predict([featurize(job)]))[0]
        predictions.append({"title": job.title, "company": job.company,
                             "industry": job.industry, "predicted_salary": pred})

    dist = {b: sum(1 for j in labeled if j["bucket"] == b) for b in BUCKETS}
    ind_avg = {}
    for j in labeled:
        ind_avg.setdefault(j["job"].industry or "Other", []).append(j["mid"])
    ind_avg = {k: round(sum(v)/len(v)) for k, v in ind_avg.items()}

    return jsonify({
        "labeled_count":    len(labeled),
        "unlabeled_count":  len(unlabeled),
        "cv_accuracy":      acc,
        "cv_folds":         cv,
        "salary_dist":      dist,
        "industry_avg":     ind_avg,
        "predictions":      predictions,
        "feature_count":    len(all_inds) + len(all_locs),
    })


@app.route("/api/dashboard/market")
def dashboard_market():
    jobs = Job.query.filter_by(is_dismissed=False).all()
    if not jobs:
        return jsonify({"error": "No jobs loaded yet."}), 404

    # Salary buckets
    salary_buckets = {"<$50k": 0, "$50–75k": 0, "$75–100k": 0,
                      "$100–130k": 0, "$130k+": 0, "Not listed": 0}
    domain_salary = {}   # industry → list of midpoints

    import re as _re
    num_re = _re.compile(r'\$([\d,]+)')

    for job in jobs:
        ind = job.industry or "Other"
        if job.salary:
            nums = [int(n.replace(",", "")) for n in num_re.findall(job.salary)]
            if nums:
                mid = sum(nums) / len(nums)
                if mid < 50000:     salary_buckets["<$50k"] += 1
                elif mid < 75000:   salary_buckets["$50–75k"] += 1
                elif mid < 100000:  salary_buckets["$75–100k"] += 1
                elif mid < 130000:  salary_buckets["$100–130k"] += 1
                else:               salary_buckets["$130k+"] += 1
                domain_salary.setdefault(ind, []).append(mid)
        else:
            salary_buckets["Not listed"] += 1

    domain_avg_salary = {
        ind: round(sum(vals) / len(vals))
        for ind, vals in domain_salary.items() if vals
    }

    # Location job density
    loc_counts = {}
    for job in jobs:
        if job.location:
            loc_counts[job.location] = loc_counts.get(job.location, 0) + 1
    top_locs = sorted(loc_counts.items(), key=lambda x: x[1], reverse=True)[:15]

    # Industry × location heatmap data
    ind_loc = {}
    for job in jobs:
        if job.industry and job.location:
            ind_loc.setdefault(job.industry, {})
            ind_loc[job.industry][job.location] = ind_loc[job.industry].get(job.location, 0) + 1

    return jsonify({
        "salary_buckets":    salary_buckets,
        "domain_avg_salary": domain_avg_salary,
        "location_density":  dict(top_locs),
        "industry_location": ind_loc,
    })


@app.route("/api/dashboard/stats")
def dashboard_stats():
    jobs    = Job.query.filter_by(is_dismissed=False).all()
    profile = _active_profile()
    if not jobs:
        return jsonify({"error": "No jobs loaded yet — upload a resume first."}), 404

    industry_counts  = {}
    location_counts  = {}
    score_buckets    = [0] * 5   # 80-83, 84-87, 88-91, 92-95, 96-99

    skills = _load_json(profile.skills)  if profile else []
    titles = _load_json(profile.job_titles) if profile else []

    scores = []
    for job in jobs:
        ind = job.industry or "Other"
        industry_counts[ind] = industry_counts.get(ind, 0) + 1
        if job.location:
            location_counts[job.location] = location_counts.get(job.location, 0) + 1
        if profile:
            s = calc_match_score(job, skills, titles)
            scores.append(s)
            bucket = min(4, (s - 80) // 4)
            score_buckets[bucket] += 1

    top_locations = sorted(location_counts.items(), key=lambda x: x[1], reverse=True)[:12]
    with_salary   = sum(1 for j in jobs if j.salary)
    saved         = sum(1 for j in jobs if j.is_saved)

    return jsonify({
        "total_jobs":          len(jobs),
        "saved":               saved,
        "with_salary":         with_salary,
        "salary_pct":          round(with_salary / len(jobs) * 100),
        "avg_match":           round(sum(scores) / len(scores)) if scores else None,
        "industry_breakdown":  industry_counts,
        "top_locations":       dict(top_locations),
        "score_distribution":  {
            "labels": ["80-83", "84-87", "88-91", "92-95", "96-99"],
            "counts": score_buckets,
        },
        "profile": {
            "skills_count":   len(skills),
            "titles":         titles,
            "education":      profile.education if profile else None,
            "experience":     profile.experience_level if profile else None,
        } if profile else None,
    })


@app.route("/api/dashboard/clusters")
def dashboard_clusters():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    jobs = Job.query.filter_by(is_dismissed=False).all()
    if len(jobs) < 8:
        return jsonify({"error": "Need at least 8 jobs to cluster. Refresh jobs first."}), 400

    texts = [f"{j.title} {j.description_snippet or ''}" for j in jobs]

    vectorizer = TfidfVectorizer(max_features=300, stop_words="english", ngram_range=(1, 2))
    X = vectorizer.fit_transform(texts)

    n_clusters = min(8, max(4, len(jobs) // 25))
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)

    try:
        sil = round(float(silhouette_score(X, labels)), 3)
    except Exception:
        sil = None

    feature_names = vectorizer.get_feature_names_out()
    clusters = []
    for i in range(n_clusters):
        top_idx    = kmeans.cluster_centers_[i].argsort()[-8:][::-1]
        top_terms  = [feature_names[idx] for idx in top_idx]
        members    = [jobs[j] for j in range(len(jobs)) if labels[j] == i]
        industries = {}
        for m in members:
            ind = m.industry or "Other"
            industries[ind] = industries.get(ind, 0) + 1
        sample = list({m.title for m in members})[:6]
        clusters.append({
            "id":         i,
            "label":      " · ".join(top_terms[:3]).title(),
            "top_terms":  top_terms,
            "count":      len(members),
            "industries": industries,
            "sample_jobs": sample,
        })

    clusters.sort(key=lambda x: x["count"], reverse=True)

    return jsonify({
        "clusters":         clusters,
        "n_clusters":       n_clusters,
        "total_jobs":       len(jobs),
        "silhouette_score": sil,
        "inertia":          round(float(kmeans.inertia_), 1),
    })


if __name__ == "__main__":
    app.run(debug=True)
