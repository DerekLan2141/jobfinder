from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    company = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(200))
    url = db.Column(db.String(500), unique=True, nullable=False)
    source = db.Column(db.String(50))  # linkedin, builtin, handshake, etc.
    description_snippet = db.Column(db.Text)
    matched_keywords = db.Column(db.String(500))
    date_posted = db.Column(db.String(100))
    date_scraped = db.Column(db.DateTime, default=datetime.utcnow)
    is_saved = db.Column(db.Boolean, default=False)
    is_dismissed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    industry = db.Column(db.String(100))
    ai_description = db.Column(db.Text)
    ai_salary_estimate = db.Column(db.String(200))

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "description_snippet": self.description_snippet,
            "matched_keywords": self.matched_keywords,
            "date_posted": self.date_posted,
            "date_scraped": self.date_scraped.isoformat(),
            "is_saved": self.is_saved,
            "is_dismissed": self.is_dismissed,
            "notes": self.notes,
            "industry": self.industry,
            "ai_description": self.ai_description,
            "ai_salary_estimate": self.ai_salary_estimate,
        }
