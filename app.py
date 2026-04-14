import os
from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from models import db, Job
from scrapers import builtin, linkedin, handshake

load_dotenv()

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///jobs.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)


def run_scrapers():
    """Run all scrapers and save new jobs to the database."""
    print("[scheduler] Running scrapers...")
    scrapers = [
        ("builtin", builtin.scrape),
        ("linkedin", linkedin.scrape),
        ("handshake", handshake.scrape),
    ]
    for name, scrape_fn in scrapers:
        try:
            results = scrape_fn()
            new_count = 0
            for job_data in results:
                if not Job.query.filter_by(url=job_data["url"]).first():
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

    query = Job.query.filter_by(is_dismissed=False) if not show_dismissed else Job.query
    if source:
        query = query.filter_by(source=source)
    if saved_only:
        query = query.filter_by(is_saved=True)

    jobs = query.order_by(Job.date_scraped.desc()).all()
    return jsonify([j.to_dict() for j in jobs])


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


@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    """Manually trigger a scrape run."""
    run_scrapers()
    return jsonify({"success": True})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=lambda: app.app_context().__enter__() or run_scrapers(),
        trigger="interval",
        hours=6,
        id="scrape_jobs",
    )
    scheduler.start()

    app.run(debug=True, use_reloader=False)
