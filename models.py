from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Job(db.Model):
    id                  = db.Column(db.Integer, primary_key=True)
    title               = db.Column(db.String(200), nullable=False)
    company             = db.Column(db.String(200), nullable=False)
    location            = db.Column(db.String(200))
    url                 = db.Column(db.String(500), unique=True, nullable=False)
    source              = db.Column(db.String(50), default="adzuna")
    description_snippet = db.Column(db.Text)
    date_posted         = db.Column(db.String(100))
    date_scraped        = db.Column(db.DateTime, default=datetime.utcnow)
    salary              = db.Column(db.String(200))
    industry            = db.Column(db.String(100))
    is_saved            = db.Column(db.Boolean, default=False)
    is_dismissed        = db.Column(db.Boolean, default=False)
    notes               = db.Column(db.Text)
    ai_description      = db.Column(db.Text)
    ai_salary_estimate  = db.Column(db.String(200))
    cv_guide            = db.Column(db.Text)

    def to_dict(self, match_score=None):
        d = {
            "id":                  self.id,
            "title":               self.title,
            "company":             self.company,
            "location":            self.location,
            "url":                 self.url,
            "source":              self.source,
            "description_snippet": self.description_snippet,
            "date_posted":         self.date_posted,
            "salary":              self.salary,
            "industry":            self.industry,
            "is_saved":            self.is_saved,
            "is_dismissed":        self.is_dismissed,
            "notes":               self.notes,
            "ai_description":      self.ai_description,
            "ai_salary_estimate":  self.ai_salary_estimate,
        }
        if match_score is not None:
            d["match_score"] = match_score
        return d


class Profile(db.Model):
    """Active resume profile — only one row (id=1)."""
    id             = db.Column(db.Integer, primary_key=True)
    skills         = db.Column(db.Text)   # JSON list[str]
    job_titles     = db.Column(db.Text)   # JSON list[str]
    search_queries = db.Column(db.Text)   # JSON list[str] — Adzuna queries
    summary        = db.Column(db.Text)
    education      = db.Column(db.String(300))
    experience_level = db.Column(db.String(50))
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
