import os
import json
import re
import uuid
from flask import Flask, render_template, jsonify, request, session
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from models import db, Job, Profile
from services.adzuna import fetch_jobs

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "jobfinder-dev-secret-change-in-prod")

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
            ("session_id",       "VARCHAR(36)"),
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
            ("session_id",         "VARCHAR(36)"),
        ]:
            try:
                _conn.execute(db.text(
                    f"ALTER TABLE job ADD COLUMN IF NOT EXISTS {_col} {_ddl}"
                ))
            except Exception:
                pass
        # Drop old global unique constraint on url (breaks multi-user: same URL
        # from Adzuna can't be stored for two different sessions)
        for _stmt in [
            "ALTER TABLE job DROP CONSTRAINT IF EXISTS job_url_key",
            "ALTER TABLE job DROP CONSTRAINT IF EXISTS uq_job_url_session",
            "DROP INDEX IF EXISTS uq_job_url_session",
        ]:
            try:
                _conn.execute(db.text(_stmt))
            except Exception:
                pass
        try:
            _conn.execute(db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_url_session ON job (url, session_id)"
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


def gemini_generate(prompt: str, temperature: float | None = None) -> str:
    from google.genai import types as _gtypes
    if temperature is None:
        try:
            temperature = session.get("gemini_temperature", 0.7)
        except RuntimeError:
            temperature = 0.7
    cfg = _gtypes.GenerateContentConfig(temperature=float(temperature))
    response = gemini_client().models.generate_content(
        model=GEMINI_MODEL, contents=prompt, config=cfg
    )
    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

def classify_industry(title: str, snippet: str = "") -> str:
    text = (title + " " + (snippet or "")).lower()
    # Order: most specific first to prevent overlap
    if any(k in text for k in ["cybersecurity","cyber security","security engineer","security analyst",
                                 "infosec","penetration test","soc analyst","threat intel","vulnerability"]):
        return "Cybersecurity"
    if any(k in text for k in ["data scientist","data analyst","machine learning","ml engineer",
                                 "ai engineer","data engineer","analytics engineer","business intelligence",
                                 "bi analyst","nlp engineer","computer vision","quantitative analyst",
                                 "quant analyst"]):
        return "Data & Analytics"
    if any(k in text for k in ["product manager","product owner","product analyst","product operations"]):
        return "Product Management"
    if any(k in text for k in ["software engineer","software developer","backend","front-end","frontend",
                                 "full stack","fullstack","full-stack","web developer","ios developer",
                                 "android developer","mobile developer","react developer","python developer",
                                 "java developer","firmware","embedded","qa engineer","quality assurance",
                                 "test engineer","software tester","automation engineer"]):
        return "Software Engineering"
    if any(k in text for k in ["devops","site reliability","sre ","cloud engineer","platform engineer",
                                 "infrastructure engineer","kubernetes","terraform","ci/cd",
                                 "network engineer","linux admin","systems engineer","reliability engineer"]):
        return "DevOps & Cloud"
    if any(k in text for k in ["it support","help desk","helpdesk","technical support",
                                 "it administrator","sysadmin","sys admin","system administrator",
                                 "it technician","it specialist","desktop support","service desk",
                                 "it coordinator","database administrator","dba "]):
        return "IT Support"
    if any(k in text for k in ["mechanical engineer","electrical engineer","civil engineer",
                                 "aerospace engineer","chemical engineer","industrial engineer",
                                 "manufacturing engineer","materials engineer","structural engineer",
                                 "process engineer","hardware engineer","rfid","robotics engineer",
                                 "controls engineer","systems integration"]):
        return "Engineering"
    if any(k in text for k in ["designer","ux designer","ui designer","ux/ui","user experience",
                                 "user interface","graphic design","visual design","product design",
                                 "motion design","brand design","illustrator","animator","multimedia"]):
        return "Design"
    if any(k in text for k in ["financial analyst","finance","accounting","investment banking",
                                 "investment analyst","banking","hedge fund","audit","tax accountant",
                                 "actuar","treasury","controller","bookkeep","cpa ","equity analyst",
                                 "risk analyst","insurance analyst","underwriter","wealth management"]):
        return "Finance"
    if any(k in text for k in ["marketing","growth hacker","seo","sem ","social media",
                                 "content marketing","digital marketing","email marketing",
                                 "demand generation","paid media","performance marketing",
                                 "community manager","brand ambassador","influencer","campaign"]):
        return "Marketing"
    if any(k in text for k in ["public relations","pr specialist","pr coordinator","communications specialist",
                                 "communications coordinator","communications manager","media relations",
                                 "corporate communications","brand communications","press","spokesperson",
                                 "external affairs"]):
        return "Communications & PR"
    if any(k in text for k in ["technical writer","content writer","copywriter","content strategist",
                                 "content creator","editor","journalist","documentation","grant writer",
                                 "proposal writer","scriptwriter"]):
        return "Content & Writing"
    if any(k in text for k in ["sales","account executive","business development","account manager",
                                 "sales representative","sales engineer","solutions engineer",
                                 "sales associate","retail","store associate","e-commerce","merchandis"]):
        return "Sales"
    if any(k in text for k in ["customer success","customer support","customer service",
                                 "client success","client support","customer experience",
                                 "support specialist","support engineer","technical account","cx analyst"]):
        return "Customer Success"
    if any(k in text for k in ["healthcare","medical","clinical","nursing","pharma","biotech",
                                 "life sciences","lab technician","health informatics","patient care",
                                 "ehr ","epidemiolog","public health","physician","therapist","radiology",
                                 "dental","hospital","health coach"]):
        return "Healthcare"
    if any(k in text for k in ["research scientist","research analyst","research associate",
                                 "research assistant","policy analyst","policy researcher","policy advisor",
                                 "public policy","policy coordinator","research coordinator",
                                 "social researcher","economic analyst","intelligence analyst"]):
        return "Research & Policy"
    if any(k in text for k in ["teaching","teacher","tutor","instructor","curriculum","academic advisor",
                                 "education coordinator","instructional designer","learning specialist",
                                 "professor","lecturer","training coordinator","e-learning","edtech"]):
        return "Education"
    if any(k in text for k in ["operations","project manager","program manager","supply chain",
                                 "logistics","business analyst","scrum master","agile coach",
                                 "process improvement","office manager","program coordinator",
                                 "project coordinator","event coordinator","administrative assistant",
                                 "executive assistant","office administrator","office coordinator",
                                 "administrative coordinator","operations analyst","management analyst",
                                 "strategy analyst","general manager","facility","coordinator"]):
        return "Operations"
    if any(k in text for k in ["human resources","recruiter","talent acquisition","people ops",
                                 "hr generalist","hr coordinator","hris","compensation","benefits",
                                 "learning and development","l&d ","onboarding","workforce"]):
        return "HR & Recruiting"
    if any(k in text for k in ["legal","attorney","paralegal","compliance","regulatory affairs",
                                 "counsel","contract manager","gdpr","privacy analyst","corporate law",
                                 "litigation","intellectual property"]):
        return "Legal & Compliance"
    return "Other"


def calc_hire_probability(job: Job, skills: list[str], experience_level: str) -> int:
    """Estimates probability (0-88%) that candidate passes initial screening for this job.
    Directionally different from match_score: measures what % of JOB requirements the candidate covers."""
    if not skills:
        return 40
    job_text = " ".join(filter(None, [job.title, job.description_snippet or ""])).lower()
    skills_lower = [s.lower() for s in skills]
    hits = sum(1 for s in skills_lower if s in job_text)
    coverage = hits / len(skills_lower)
    base = 20 + round(coverage * 60)
    title_lower = job.title.lower()
    is_senior_job = any(w in title_lower for w in
                        ["senior", "lead", "principal", "staff", "head", "director", "vp", "manager"])
    is_entry_job  = any(w in title_lower for w in
                        ["junior", "entry", "associate", "intern", "graduate", "assistant"])
    exp = (experience_level or "").lower()
    if is_senior_job and exp in ("entry", "junior"):
        base -= 18
    elif is_entry_job and exp in ("senior", "mid"):
        base += 8
    elif is_senior_job and exp in ("senior", "mid"):
        base += 6
    return max(5, min(88, base))


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


def get_uid() -> str:
    if "uid" not in session:
        session["uid"] = str(uuid.uuid4())
    return session["uid"]


def _active_profile() -> Profile | None:
    return Profile.query.filter_by(session_id=get_uid()).first()


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
    uid = get_uid()
    Profile.query.filter_by(session_id=uid).delete()
    Job.query.filter(Job.session_id == uid, Job.is_saved == False).delete()
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
  "search_queries": ["8-10 concise Adzuna job search queries based on this person's skills and target roles. Use short skill-focused terms — do NOT add experience level words like 'entry level', 'junior', or 'senior' as they reduce results. Good examples: 'python developer', 'data analyst SQL', 'software engineer React', 'machine learning engineer', 'financial analyst Excel', 'product manager SaaS'. Vary the queries to cover different skill combinations and related roles so we get a broad set of listings."]
}}"""

        data = json.loads(gemini_generate(prompt))

        uid = get_uid()
        profile = Profile.query.filter_by(session_id=uid).first()
        if profile is None:
            profile = Profile(session_id=uid)
            db.session.add(profile)

        profile.skills           = json.dumps(data.get("skills", []))
        profile.job_titles       = json.dumps(data.get("job_titles", []))
        profile.search_queries   = json.dumps(data.get("search_queries", []))
        profile.summary          = data.get("summary", "")
        profile.education        = data.get("education", "")
        profile.experience_level = data.get("experience_level", "")
        profile.resume_text      = text[:6000]
        db.session.commit()

        queries = data.get("search_queries", [])
        if queries:
            _refresh_jobs(queries, uid)

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
    uid = get_uid()
    Profile.query.filter_by(session_id=uid).delete()
    Job.query.filter(Job.session_id == uid, Job.is_saved == False).delete()
    db.session.commit()
    return jsonify({"success": True})


def _refresh_jobs(queries: list[str], session_id: str, limit: int = 10, max_pages: int = 3):
    """Fetch jobs from Adzuna and store new ones scoped to this session."""
    results = fetch_jobs(queries[:limit], results_per_page=50, max_pages=max_pages)
    count = 0
    for data in results:
        if Job.query.filter_by(url=data["url"], session_id=session_id).first():
            continue
        data["industry"]   = classify_industry(data.get("title", ""), data.get("description_snippet", ""))
        data["source"]     = "adzuna"
        data["session_id"] = session_id
        job = Job(**{k: v for k, v in data.items() if hasattr(Job, k)})
        db.session.add(job)
        count += 1
    db.session.commit()
    print(f"[refresh] Added {count} new jobs for session {session_id[:8]}")


@app.route("/api/jobs")
def get_jobs():
    saved_only = request.args.get("saved") == "true"
    visa_only  = request.args.get("visa") == "true"
    search     = request.args.get("search", "").strip()
    location   = request.args.get("location", "").strip()
    industry   = request.args.get("industry", "").strip()

    uid   = get_uid()
    query = Job.query.filter_by(is_dismissed=False, session_id=uid)
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
        exp    = profile.experience_level or ""
        scored = [
            (j, calc_match_score(j, skills, titles), calc_hire_probability(j, skills, exp))
            for j in jobs
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return jsonify([
            {**j.to_dict(match_score=s), "hire_probability": hp}
            for j, s, hp in scored
        ])

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
        _refresh_jobs(queries, get_uid(), limit=len(queries), max_pages=3)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filters")
def get_filters():
    uid = get_uid()
    industry_rows = (
        db.session.query(Job.industry)
        .filter(Job.session_id == uid, Job.industry.isnot(None), Job.industry != "", Job.industry != "Other")
        .distinct().all()
    )
    location_rows = (
        db.session.query(Job.location)
        .filter(Job.session_id == uid, Job.location.isnot(None), Job.location != "")
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
    uid = get_uid()
    candidates = Job.query.filter(Job.id != job_id, Job.is_dismissed == False, Job.session_id == uid).all()
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
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import cross_val_score
    import numpy as np

    uid    = get_uid()
    jobs   = Job.query.filter_by(is_dismissed=False, session_id=uid).all()
    num_re = re.compile(r'\$([\d,]+)')

    # ── Split labeled (have salary) vs unlabeled ──────────────────────────
    labeled, unlabeled = [], []
    for job in jobs:
        if job.salary:
            nums = [int(n.replace(",", "")) for n in num_re.findall(job.salary)]
            if nums:
                labeled.append({"job": job, "mid": sum(nums) / len(nums)})
                continue
        unlabeled.append(job)

    if len(labeled) < 6:
        return jsonify({"error": f"Only {len(labeled)} jobs have salary data — need at least 6 to train. Try Refresh Jobs."}), 400

    # ── Feature names & one-hot vectors ──────────────────────────────────
    # Features: one binary flag per unique industry + one per unique location
    all_inds  = sorted(set(j["job"].industry or "Other" for j in labeled))
    all_locs  = sorted(set(j["job"].location or "Unknown" for j in labeled))
    feat_names = [f"industry:{i}" for i in all_inds] + [f"location:{l}" for l in all_locs]

    def featurize(job):
        return (
            [1 if (job.industry or "Other") == i else 0 for i in all_inds] +
            [1 if (job.location or "Unknown") == l else 0 for l in all_locs]
        )

    X = [featurize(j["job"]) for j in labeled]
    y = [j["mid"] for j in labeled]          # continuous salary midpoints

    # ── Linear Regression + cross-validation ─────────────────────────────
    reg = LinearRegression()
    cv  = min(5, len(labeled) // 2)
    if cv >= 2:
        r2  = round(float(cross_val_score(reg, X, y, cv=cv, scoring="r2").mean()), 3)
        mae = round(float(-cross_val_score(
            reg, X, y, cv=cv, scoring="neg_mean_absolute_error").mean()))
    else:
        r2 = mae = None

    reg.fit(X, y)

    # ── Top salary-driving features (largest positive coefficients) ───────
    coef_pairs = sorted(zip(feat_names, reg.coef_), key=lambda x: x[1], reverse=True)
    top_positive = [{"name": n.split(":", 1)[1], "type": n.split(":")[0],
                     "effect": f"+${int(abs(v)):,}"}
                    for n, v in coef_pairs[:5] if v > 0]
    top_negative = [{"name": n.split(":", 1)[1], "type": n.split(":")[0],
                     "effect": f"-${int(abs(v)):,}"}
                    for n, v in reversed(coef_pairs) if v < 0][:3]

    # ── Predict salary for unlabeled jobs ─────────────────────────────────
    predictions = []
    for job in unlabeled[:30]:
        raw = reg.predict([featurize(job)])[0]
        predicted = max(25000, int(round(raw / 1000) * 1000))
        predictions.append({
            "title":            job.title,
            "company":          job.company,
            "industry":         job.industry,
            "predicted_salary": f"~${predicted:,}",
        })

    # ── Bell curve: fine histogram + fitted normal distribution ──────────
    import math
    mids        = [j["mid"] for j in labeled]
    sal_mean    = float(np.mean(mids))
    sal_std     = float(np.std(mids)) if len(mids) > 1 else 1.0
    bin_width   = 10_000
    lo          = max(0,       int((sal_mean - 3.5 * sal_std) // bin_width) * bin_width)
    hi          = min(300_000, int((sal_mean + 3.5 * sal_std) // bin_width + 1) * bin_width)
    bin_edges   = list(range(lo, hi + bin_width, bin_width))
    bin_centers = [(bin_edges[i] + bin_edges[i + 1]) / 2 for i in range(len(bin_edges) - 1)]
    bell_labels = [f"${int(c)//1000}k" for c in bin_centers]
    bell_hist   = [sum(1 for m in mids if bin_edges[i] <= m < bin_edges[i + 1])
                   for i in range(len(bin_edges) - 1)]
    bell_curve  = [
        round((1 / (sal_std * math.sqrt(2 * math.pi)))
              * math.exp(-0.5 * ((c - sal_mean) / sal_std) ** 2)
              * len(mids) * bin_width, 4)
        for c in bin_centers
    ]

    # ── Industry averages ─────────────────────────────────────────────────
    ind_avg = {}
    for j in labeled:
        ind_avg.setdefault(j["job"].industry or "Other", []).append(j["mid"])
    ind_avg = {k: round(sum(v) / len(v)) for k, v in ind_avg.items()}

    return jsonify({
        "labeled_count":   len(labeled),
        "unlabeled_count": len(unlabeled),
        "cv_r2":           r2,
        "cv_mae":          mae,
        "cv_folds":        cv,
        "salary_mean":     round(sal_mean),
        "salary_std":      round(sal_std),
        "bell_labels":     bell_labels,
        "bell_hist":       bell_hist,
        "bell_curve":      bell_curve,
        "industry_avg":    ind_avg,
        "predictions":     predictions,
        "feature_count":   len(feat_names),
        "intercept":       round(reg.intercept_),
        "top_positive":    top_positive,
        "top_negative":    top_negative,
    })


@app.route("/api/dashboard/market")
def dashboard_market():
    uid  = get_uid()
    jobs = Job.query.filter_by(is_dismissed=False, session_id=uid).all()
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

    # Best paying (industry, location) combos
    ind_loc_salary = {}
    for job in jobs:
        if job.salary and job.industry and job.location:
            nums = [int(n.replace(",", "")) for n in num_re.findall(job.salary)]
            if nums:
                key = (job.industry, job.location)
                ind_loc_salary.setdefault(key, []).append(sum(nums) / len(nums))

    top_salary_combos = sorted(
        [{"industry": k[0], "location": k[1],
          "avg_salary": round(sum(v) / len(v)), "count": len(v)}
         for k, v in ind_loc_salary.items()],
        key=lambda x: x["avg_salary"],
        reverse=True,
    )[:12]

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
        "top_salary_combos": top_salary_combos,
    })


@app.route("/api/dashboard/stats")
def dashboard_stats():
    uid     = get_uid()
    jobs    = Job.query.filter_by(is_dismissed=False, session_id=uid).all()
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
    import numpy as np

    uid  = get_uid()
    jobs = Job.query.filter_by(is_dismissed=False, session_id=uid).all()
    if len(jobs) < 4:
        return jsonify({"error": "Need at least 4 jobs to cluster. Refresh jobs first."}), 400

    texts = [f"{j.title} {j.description_snippet or ''}" for j in jobs]
    vectorizer = TfidfVectorizer(max_features=300, stop_words="english", ngram_range=(1, 2))
    X = vectorizer.fit_transform(texts)
    feature_names = vectorizer.get_feature_names_out()

    # Each cluster must have at least this many jobs — prevents one giant + tiny slivers
    min_size = max(3, len(jobs) // 8)
    max_k    = min(6, len(jobs) // min_size)

    best_k, best_km, best_labels, best_sil = 1, None, None, -1.0

    for k in range(2, max_k + 1):
        km   = KMeans(n_clusters=k, random_state=42, n_init=10)
        lbls = km.fit_predict(X)
        if min(int(np.sum(lbls == i)) for i in range(k)) < min_size:
            continue          # skip — at least one cluster is too small
        try:
            s = float(silhouette_score(X, lbls))
        except Exception:
            continue
        if s > best_sil:
            best_sil, best_k, best_km, best_labels = s, k, km, lbls

    def _build_cluster(members, center_vec):
        top_idx   = center_vec.argsort()[-8:][::-1]
        top_terms = [feature_names[idx] for idx in top_idx]
        industries = {}
        for m in members:
            ind = m.industry or "Other"
            industries[ind] = industries.get(ind, 0) + 1
        return {
            "label":      " · ".join(top_terms[:3]).title(),
            "top_terms":  top_terms,
            "count":      len(members),
            "industries": industries,
            "sample_jobs": list({m.title for m in members})[:6],
        }

    if best_k == 1:
        # No valid multi-cluster split — show everything as one group
        centroid = np.asarray(X.mean(axis=0)).flatten()
        clusters = [_build_cluster(jobs, centroid)]
        return jsonify({
            "clusters":         clusters,
            "n_clusters":       1,
            "total_jobs":       len(jobs),
            "silhouette_score": None,
            "inertia":          None,
            "note":             "Jobs are too similar to split into meaningful sub-groups — showing as one cluster.",
        })

    clusters = []
    for i in range(best_k):
        members = [jobs[j] for j in range(len(jobs)) if best_labels[j] == i]
        clusters.append(_build_cluster(members, best_km.cluster_centers_[i]))
    clusters.sort(key=lambda x: x["count"], reverse=True)

    return jsonify({
        "clusters":         clusters,
        "n_clusters":       best_k,
        "total_jobs":       len(jobs),
        "silhouette_score": round(best_sil, 3),
        "inertia":          round(float(best_km.inertia_), 1),
    })


@app.route("/api/export/csv")
def export_csv():
    import csv, io as _io
    from flask import Response
    uid  = get_uid()
    jobs = Job.query.filter_by(is_dismissed=False, session_id=uid).all()
    if not jobs:
        return jsonify({"error": "No jobs to export"}), 400
    profile = _active_profile()
    skills  = _load_json(profile.skills) if profile else []
    exp     = profile.experience_level or "" if profile else ""
    out = _io.StringIO()
    w   = csv.writer(out)
    w.writerow([
        "title", "company", "location", "industry", "salary",
        "match_score", "hire_probability", "url",
        "date_posted", "source", "is_saved", "notes",
    ])
    for j in jobs:
        ms = calc_match_score(j, skills, _load_json(profile.job_titles) if profile else []) if profile else ""
        hp = calc_hire_probability(j, skills, exp) if profile else ""
        w.writerow([
            j.title, j.company, j.location, j.industry, j.salary,
            ms, hp, j.url, j.date_posted, j.source, j.is_saved, j.notes or "",
        ])
    out.seek(0)
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobfinder_jobs.csv"},
    )


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "GET":
        return jsonify({"temperature": session.get("gemini_temperature", 0.7)})
    data = request.json or {}
    temp = data.get("temperature")
    if temp is not None:
        try:
            t = float(temp)
            if not (0.0 <= t <= 2.0):
                raise ValueError
            session["gemini_temperature"] = t
            session.modified = True
        except (ValueError, TypeError):
            return jsonify({"error": "temperature must be 0.0–2.0"}), 400
    return jsonify({"success": True, "temperature": session.get("gemini_temperature", 0.7)})


@app.route("/batch")
def batch_page():
    return render_template("batch.html")


@app.route("/api/batch/upload", methods=["POST"])
def batch_upload():
    import pypdf, io as _io
    files = request.files.getlist("resumes")
    if not files or not files[0].filename:
        return jsonify({"error": "No files uploaded"}), 400
    if len(files) > 10:
        return jsonify({"error": "Maximum 10 resumes per batch"}), 400
    if not os.getenv("GEMINI_API_KEY"):
        return jsonify({"error": "GEMINI_API_KEY not configured"}), 500

    results = []
    for file in files:
        fname = file.filename or "unknown.pdf"
        if not fname.lower().endswith(".pdf"):
            results.append({"filename": fname, "error": "Not a PDF — skipped"})
            continue
        try:
            reader = pypdf.PdfReader(_io.BytesIO(file.read()))
            text   = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            if not text:
                results.append({"filename": fname, "error": "Could not extract text"})
                continue

            prompt = f"""Analyze this resume for a job-market comparison. Return ONLY valid JSON.

Resume:
{text[:6000]}

Return exactly:
{{
  "skills": ["all technical skills, tools, languages, frameworks listed"],
  "job_titles": ["held or targeted job titles"],
  "experience_level": "entry/junior/mid/senior",
  "education": "highest degree and field",
  "years_of_experience": "estimated total years (number or range, e.g. '2-3')",
  "summary": "1-2 sentence professional summary",
  "top_industries": ["top 2-3 industries this person fits"],
  "strengths": ["top 3 standout strengths from the resume"],
  "gaps": ["top 2-3 skill gaps or weaknesses relative to tech/professional job market"]
}}"""

            data             = json.loads(gemini_generate(prompt, temperature=0.3))
            data["filename"] = fname

            # ── Score this resume against the session's job pool ──────────
            uid       = get_uid()
            pool      = Job.query.filter_by(is_dismissed=False, session_id=uid).all()
            if pool:
                import numpy as _np
                r_skills = data.get("skills", [])
                r_titles = data.get("job_titles", [])
                r_exp    = data.get("experience_level", "")
                m_scores = [calc_match_score(j, r_skills, r_titles) for j in pool]
                h_scores = [calc_hire_probability(j, r_skills, r_exp)  for j in pool]

                top_jobs = sorted(
                    zip(pool, m_scores, h_scores), key=lambda x: x[1], reverse=True
                )[:5]

                data["match_metrics"] = {
                    "job_count":    len(pool),
                    "avg_match":    round(float(_np.mean(m_scores)), 1),
                    "max_match":    int(max(m_scores)),
                    "min_match":    int(min(m_scores)),
                    "score_std":    round(float(_np.std(m_scores)), 2),
                    "avg_hire_prob": round(float(_np.mean(h_scores)), 1),
                    "high_match_pct": round(sum(1 for s in m_scores if s >= 90) / len(m_scores) * 100, 1),
                    "score_dist": {
                        "80-84": sum(1 for s in m_scores if 80 <= s < 85),
                        "85-89": sum(1 for s in m_scores if 85 <= s < 90),
                        "90-94": sum(1 for s in m_scores if 90 <= s < 95),
                        "95-99": sum(1 for s in m_scores if s >= 95),
                    },
                    "top_jobs": [
                        {"title": j.title, "company": j.company,
                         "industry": j.industry or "—",
                         "match": ms, "hire_prob": hp}
                        for j, ms, hp in top_jobs
                    ],
                }

            results.append(data)
        except Exception as e:
            results.append({"filename": fname, "error": str(e)})

    return jsonify({"results": results, "count": len(results)})


@app.route("/api/admin/report.csv")
def admin_report():
    """
    Full analytics export — protected by ADMIN_KEY env var.
    Access: /api/admin/report.csv?key=<your ADMIN_KEY>
    Sections: Overview · Industry · Location · Salary Model · Cluster Analysis
    """
    import csv, io as _io, math
    from flask import Response
    from sklearn.linear_model import LinearRegression
    from sklearn.model_selection import cross_val_score
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    import numpy as np

    # ── Auth ──────────────────────────────────────────────────────────────────
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key:
        return "ADMIN_KEY not set in environment.", 403
    if request.args.get("key", "") != admin_key:
        return "Forbidden.", 403

    uid  = get_uid()
    jobs = Job.query.filter_by(is_dismissed=False, session_id=uid).all()
    if not jobs:
        return "No jobs in this session.", 404

    profile = _active_profile()
    skills  = _load_json(profile.skills)  if profile else []
    titles  = _load_json(profile.job_titles) if profile else []
    exp     = profile.experience_level or "" if profile else ""

    out = _io.StringIO()
    w   = csv.writer(out)

    def blank(n=1):
        for _ in range(n):
            w.writerow([])

    def section(title):
        blank()
        w.writerow([f"=== {title} ==="])

    # ── SECTION 1: Overview ───────────────────────────────────────────────────
    section("OVERVIEW")
    w.writerow(["Metric", "Value"])

    total      = len(jobs)
    saved      = sum(1 for j in jobs if j.is_saved)
    with_sal   = sum(1 for j in jobs if j.salary)
    industries = {}
    locs       = {}
    for j in jobs:
        industries[j.industry or "Other"] = industries.get(j.industry or "Other", 0) + 1
        if j.location:
            locs[j.location] = locs.get(j.location, 0) + 1

    scores = [calc_match_score(j, skills, titles) for j in jobs] if profile else []
    hps    = [calc_hire_probability(j, skills, exp) for j in jobs] if profile else []

    w.writerow(["Total Jobs",          total])
    w.writerow(["Saved Jobs",          saved])
    w.writerow(["Jobs with Salary",    with_sal])
    w.writerow(["Salary Coverage %",   round(with_sal / total * 100) if total else 0])
    w.writerow(["Unique Industries",   len(industries)])
    w.writerow(["Unique Locations",    len(locs)])
    if scores:
        w.writerow(["Avg Match Score",     round(sum(scores) / len(scores), 1)])
        w.writerow(["Min Match Score",     min(scores)])
        w.writerow(["Max Match Score",     max(scores)])
    if hps:
        w.writerow(["Avg Hire Probability", round(sum(hps) / len(hps), 1)])
    if profile:
        w.writerow(["Experience Level",   profile.experience_level or "—"])
        w.writerow(["Education",          profile.education or "—"])
        w.writerow(["Skills Detected",    len(skills)])
        w.writerow(["Target Titles",      "; ".join(titles)])

    # ── SECTION 2: Industry Breakdown ─────────────────────────────────────────
    section("INDUSTRY BREAKDOWN")
    w.writerow(["Industry", "Job Count", "% of Total", "Avg Match Score", "Avg Hire Probability"])
    for ind, cnt in sorted(industries.items(), key=lambda x: x[1], reverse=True):
        ind_jobs   = [j for j in jobs if (j.industry or "Other") == ind]
        ind_scores = [calc_match_score(j, skills, titles) for j in ind_jobs] if profile else []
        ind_hps    = [calc_hire_probability(j, skills, exp) for j in ind_jobs] if profile else []
        w.writerow([
            ind, cnt,
            round(cnt / total * 100, 1),
            round(sum(ind_scores) / len(ind_scores), 1) if ind_scores else "",
            round(sum(ind_hps) / len(ind_hps), 1) if ind_hps else "",
        ])

    # ── SECTION 3: Location Distribution ─────────────────────────────────────
    section("LOCATION DISTRIBUTION")
    w.writerow(["Location", "Job Count", "% of Total"])
    for loc, cnt in sorted(locs.items(), key=lambda x: x[1], reverse=True):
        w.writerow([loc, cnt, round(cnt / total * 100, 1)])

    # ── SECTION 4: Salary Hotspots ────────────────────────────────────────────
    section("SALARY HOTSPOTS — INDUSTRY × LOCATION")
    w.writerow(["Rank", "Industry", "Location", "Avg Salary ($)", "Job Count"])
    num_re = re.compile(r'\$([\d,]+)')
    ind_loc_sal = {}
    for job in jobs:
        if job.salary and job.industry and job.location:
            nums = [int(n.replace(",", "")) for n in num_re.findall(job.salary)]
            if nums:
                key = (job.industry, job.location)
                ind_loc_sal.setdefault(key, []).append(sum(nums) / len(nums))
    combos = sorted(
        [(k[0], k[1], round(sum(v) / len(v)), len(v)) for k, v in ind_loc_sal.items()],
        key=lambda x: x[2], reverse=True,
    )
    for rank, (ind, loc, avg, cnt) in enumerate(combos, 1):
        w.writerow([rank, ind, loc, avg, cnt])
    if not combos:
        w.writerow(["", "No salary+location data available"])

    # ── SECTION 5: Linear Regression — Salary Model ───────────────────────────
    section("LINEAR REGRESSION — SALARY MODEL")

    labeled = []
    for job in jobs:
        if job.salary:
            nums = [int(n.replace(",", "")) for n in num_re.findall(job.salary)]
            if nums:
                labeled.append({"job": job, "mid": sum(nums) / len(nums)})

    if len(labeled) >= 6:
        all_inds  = sorted(set(j["job"].industry or "Other" for j in labeled))
        all_locs  = sorted(set(j["job"].location or "Unknown" for j in labeled))
        feat_names = [f"industry:{i}" for i in all_inds] + [f"location:{l}" for l in all_locs]

        def featurize(job):
            return (
                [1 if (job.industry or "Other") == i else 0 for i in all_inds] +
                [1 if (job.location or "Unknown") == l else 0 for l in all_locs]
            )

        X = [featurize(j["job"]) for j in labeled]
        y = [j["mid"] for j in labeled]
        reg = LinearRegression()
        cv  = min(5, len(labeled) // 2)

        if cv >= 2:
            r2  = round(float(cross_val_score(reg, X, y, cv=cv, scoring="r2").mean()), 4)
            mae = round(float(-cross_val_score(reg, X, y, cv=cv, scoring="neg_mean_absolute_error").mean()), 2)
            mse = round(float(-cross_val_score(reg, X, y, cv=cv, scoring="neg_mean_squared_error").mean()), 2)
            rmse = round(math.sqrt(mse), 2)
        else:
            r2 = mae = mse = rmse = "N/A (need more data)"

        reg.fit(X, y)

        mids     = [j["mid"] for j in labeled]
        sal_mean = round(float(np.mean(mids)), 2)
        sal_std  = round(float(np.std(mids)), 2)

        w.writerow(["Metric", "Value", "Notes"])
        w.writerow(["Training Samples",  len(labeled), "jobs with listed salary"])
        w.writerow(["Features",          len(feat_names), "industry + location one-hot"])
        w.writerow(["Cross-Val Folds",   cv])
        w.writerow(["R² (CV)",           r2, "1.0 = perfect, 0 = no fit"])
        w.writerow(["MAE (CV)",          f"${mae:,.2f}" if isinstance(mae, float) else mae, "mean absolute error"])
        w.writerow(["MSE (CV)",          f"${mse:,.2f}" if isinstance(mse, float) else mse, "mean squared error"])
        w.writerow(["RMSE (CV)",         f"${rmse:,.2f}" if isinstance(rmse, float) else rmse, "root mean squared error"])
        w.writerow(["Intercept",         f"${reg.intercept_:,.2f}", "baseline salary"])
        w.writerow(["Salary Mean",       f"${sal_mean:,.2f}"])
        w.writerow(["Salary Std Dev",    f"${sal_std:,.2f}"])

        blank()
        w.writerow(["--- Feature Coefficients ---"])
        w.writerow(["Feature", "Type", "Coefficient ($)", "Direction"])
        coef_pairs = sorted(zip(feat_names, reg.coef_), key=lambda x: x[1], reverse=True)
        for name, coef in coef_pairs:
            ftype = name.split(":")[0]
            fname = name.split(":", 1)[1]
            direction = "+" if coef > 0 else "-"
            w.writerow([fname, ftype, round(coef, 2), direction])

        blank()
        w.writerow(["--- Salary Predictions (unlabeled jobs) ---"])
        w.writerow(["Title", "Company", "Industry", "Location", "Predicted Salary ($)"])
        unlabeled = [j for j in jobs if not j.salary or not num_re.findall(j.salary or "")]
        for job in unlabeled[:50]:
            raw = reg.predict([featurize(job)])[0]
            predicted = max(25000, int(round(raw / 1000) * 1000))
            w.writerow([job.title, job.company, job.industry or "—", job.location or "—", predicted])
    else:
        w.writerow(["Status", f"Insufficient data — only {len(labeled)} jobs have salary (need ≥ 6)"])

    # ── SECTION 6: All Jobs with Scores ───────────────────────────────────────
    section("ALL JOBS — FULL DETAIL")
    w.writerow([
        "Title", "Company", "Location", "Industry", "Salary",
        "Match Score", "Hire Probability", "Saved",
        "Date Posted", "Source", "URL", "Notes",
    ])
    for j in jobs:
        ms = calc_match_score(j, skills, titles) if profile else ""
        hp = calc_hire_probability(j, skills, exp) if profile else ""
        w.writerow([
            j.title, j.company, j.location or "", j.industry or "", j.salary or "",
            ms, hp, j.is_saved,
            j.date_posted or "", j.source or "", j.url, j.notes or "",
        ])

    # ── SECTION 7: K-Means Clustering ─────────────────────────────────────────
    section("K-MEANS CLUSTERING")

    if len(jobs) >= 4:
        texts      = [f"{j.title} {j.description_snippet or ''}" for j in jobs]
        vectorizer = TfidfVectorizer(max_features=300, stop_words="english", ngram_range=(1, 2))
        X_cl = vectorizer.fit_transform(texts)
        feat_cl = vectorizer.get_feature_names_out()

        min_size = max(3, len(jobs) // 8)
        max_k    = min(6, len(jobs) // min_size)
        best_k, best_km, best_labels, best_sil = 1, None, None, -1.0

        for k in range(2, max_k + 1):
            km   = KMeans(n_clusters=k, random_state=42, n_init=10)
            lbls = km.fit_predict(X_cl)
            if min(int(np.sum(lbls == i)) for i in range(k)) < min_size:
                continue
            try:
                s = float(silhouette_score(X_cl, lbls))
            except Exception:
                continue
            if s > best_sil:
                best_sil, best_k, best_km, best_labels = s, k, km, lbls

        w.writerow(["Metric", "Value"])
        w.writerow(["Clusters (K)",      best_k])
        w.writerow(["Total Jobs",        len(jobs)])
        w.writerow(["Silhouette Score",  round(best_sil, 4) if best_k > 1 else "N/A (single cluster)"])
        w.writerow(["Inertia",           round(float(best_km.inertia_), 2) if best_km else "N/A"])

        blank()
        w.writerow(["--- Cluster Details ---"])
        w.writerow(["Cluster", "Size", "% of Total", "Top Terms", "Industries"])

        if best_k == 1:
            centroid = np.asarray(X_cl.mean(axis=0)).flatten()
            top_idx  = centroid.argsort()[-8:][::-1]
            terms    = "; ".join(feat_cl[i] for i in top_idx)
            inds     = {}
            for j in jobs:
                inds[j.industry or "Other"] = inds.get(j.industry or "Other", 0) + 1
            ind_str = "; ".join(f"{k}({v})" for k, v in sorted(inds.items(), key=lambda x: x[1], reverse=True))
            w.writerow([1, len(jobs), 100, terms, ind_str])
        else:
            for i in range(best_k):
                members  = [jobs[j] for j in range(len(jobs)) if best_labels[j] == i]
                top_idx  = best_km.cluster_centers_[i].argsort()[-8:][::-1]
                terms    = "; ".join(feat_cl[idx] for idx in top_idx)
                inds     = {}
                for m in members:
                    inds[m.industry or "Other"] = inds.get(m.industry or "Other", 0) + 1
                ind_str = "; ".join(f"{k}({v})" for k, v in sorted(inds.items(), key=lambda x: x[1], reverse=True))
                w.writerow([
                    i + 1, len(members),
                    round(len(members) / len(jobs) * 100, 1),
                    terms, ind_str,
                ])

        blank()
        w.writerow(["--- Job → Cluster Assignment ---"])
        w.writerow(["Title", "Company", "Industry", "Cluster"])
        if best_k == 1:
            for j in jobs:
                w.writerow([j.title, j.company, j.industry or "—", 1])
        else:
            for idx, j in enumerate(jobs):
                w.writerow([j.title, j.company, j.industry or "—", int(best_labels[idx]) + 1])
    else:
        w.writerow(["Status", "Not enough jobs to cluster (need ≥ 4)"])

    # ── SECTION 8: Matching Model Accuracy Evaluation ─────────────────────────
    section("MATCHING MODEL ACCURACY EVALUATION")
    w.writerow(["Metric", "Value", "Interpretation"])

    if profile and jobs:
        all_ms = [calc_match_score(j, skills, titles) for j in jobs]
        all_hp = [calc_hire_probability(j, skills, exp) for j in jobs]

        ms_mean   = round(float(np.mean(all_ms)), 2)
        ms_std    = round(float(np.std(all_ms)), 2)
        ms_min    = int(min(all_ms))
        ms_max    = int(max(all_ms))
        ms_range  = ms_max - ms_min
        hp_mean   = round(float(np.mean(all_hp)), 2)
        hp_std    = round(float(np.std(all_hp)), 2)

        high_match   = sum(1 for s in all_ms if s >= 90)
        mid_match    = sum(1 for s in all_ms if 85 <= s < 90)
        low_match    = sum(1 for s in all_ms if s < 85)
        pct_high     = round(high_match / len(all_ms) * 100, 1)

        # Discriminative power: ratio of std to max-possible range (0-19 in 80-99 scale)
        disc_power = round(ms_std / 9.5 * 100, 1)  # normalised to 0-100%

        # Skill coverage per job: what % of job keywords appear in resume skills
        coverages = []
        for job in jobs:
            job_text  = " ".join(filter(None, [job.title, job.description_snippet or ""])).lower()
            skill_hits = sum(1 for s in skills if s.lower() in job_text)
            coverages.append(skill_hits / len(skills) * 100 if skills else 0)
        avg_coverage = round(float(np.mean(coverages)), 1)
        max_coverage = round(float(max(coverages)), 1)

        w.writerow(["--- Match Score Statistics ---", "", ""])
        w.writerow(["Mean Match Score",     ms_mean,  "Average relevance of jobs to your resume"])
        w.writerow(["Std Dev (Match)",       ms_std,   "Higher = model is more selective / discriminating"])
        w.writerow(["Min Score",             ms_min,   "Least relevant job found"])
        w.writerow(["Max Score",             ms_max,   "Best matching job found"])
        w.writerow(["Score Range",           ms_range, "Spread between best and worst match"])
        w.writerow(["Discriminative Power",  f"{disc_power}%",
                    "How well the model differentiates between jobs (higher is better)"])
        blank()
        w.writerow(["--- Hire Probability Statistics ---", "", ""])
        w.writerow(["Mean Hire Probability",  hp_mean, "Avg estimated screening pass rate"])
        w.writerow(["Std Dev (Hire Prob)",    hp_std,  "Spread — higher means your fit varies a lot across jobs"])
        w.writerow(["Min Hire Probability",   int(min(all_hp))])
        w.writerow(["Max Hire Probability",   int(max(all_hp))])
        blank()
        w.writerow(["--- Score Distribution (Match) ---", "", ""])
        w.writerow(["Tier",        "Count", "% of Jobs"])
        w.writerow(["High  (90-99)",  high_match, f"{pct_high}%"])
        w.writerow(["Mid   (85-89)",  mid_match,  f"{round(mid_match/len(all_ms)*100,1)}%"])
        w.writerow(["Low   (80-84)",  low_match,  f"{round(low_match/len(all_ms)*100,1)}%"])
        blank()
        w.writerow(["--- Skill Coverage ---", "", ""])
        w.writerow(["Avg Skill Coverage per Job", f"{avg_coverage}%",
                    "% of your skills that appear in each job on average"])
        w.writerow(["Best Job Coverage",           f"{max_coverage}%",
                    "Your skills in the best-matching job"])
        w.writerow(["Skills in Profile",           len(skills)])
        blank()
        w.writerow(["--- Per-Job Scores ---"])
        w.writerow(["Title", "Company", "Industry", "Match Score", "Hire Probability",
                    "Skill Coverage %"])
        for job, ms, hp, cov in sorted(
            zip(jobs, all_ms, all_hp, coverages), key=lambda x: x[1], reverse=True
        ):
            w.writerow([job.title, job.company, job.industry or "—",
                        ms, hp, round(cov, 1)])
    else:
        w.writerow(["Status", "Upload a resume first to evaluate matching accuracy"])

    out.seek(0)
    from flask import Response
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobfinder_admin_report.csv"},
    )


if __name__ == "__main__":
    app.run(debug=True)
