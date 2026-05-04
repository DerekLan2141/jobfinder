"""
Batch resume tester — runs a folder of PDFs through the JobFinder model
and writes all results to a single CSV.

Usage:
    python batch_test.py --folder /path/to/resumes --out results.csv

Requirements: requests  (pip install requests)

The app must be running locally (python app.py) before you run this.
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

BASE_URL   = "http://localhost:5000"
BATCH_SIZE = 10   # max per request — matches server limit
DELAY      = 1.5  # seconds between batches (be nice to Gemini rate limits)


def run_batch(session: requests.Session, pdf_paths: list[Path]) -> list[dict]:
    files = [("resumes", (p.name, p.open("rb"), "application/pdf")) for p in pdf_paths]
    try:
        resp = session.post(f"{BASE_URL}/api/batch/upload", files=files, timeout=120)
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.exceptions.Timeout:
        return [{"filename": p.name, "error": "timeout"} for p in pdf_paths]
    except Exception as e:
        return [{"filename": p.name, "error": str(e)} for p in pdf_paths]
    finally:
        for _, (_, fh, _) in files:
            fh.close()


def flatten(result: dict) -> dict:
    """Flatten nested match_metrics into a single row dict."""
    m = result.get("match_metrics") or {}
    dist = m.get("score_dist") or {}
    top  = (m.get("top_jobs") or [{}])[0]
    return {
        "filename":           result.get("filename", ""),
        "error":              result.get("error", ""),
        "experience_level":   result.get("experience_level", ""),
        "years_experience":   result.get("years_of_experience", ""),
        "education":          result.get("education", ""),
        "skills_count":       len(result.get("skills", [])),
        "skills":             "; ".join(result.get("skills", [])),
        "job_titles":         "; ".join(result.get("job_titles", [])),
        "top_industries":     "; ".join(result.get("top_industries", [])),
        "strengths":          "; ".join(result.get("strengths", [])),
        "gaps":               "; ".join(result.get("gaps", [])),
        "summary":            result.get("summary", ""),
        # Matching metrics
        "job_pool_size":      m.get("job_count", ""),
        "avg_match_score":    m.get("avg_match", ""),
        "max_match_score":    m.get("max_match", ""),
        "min_match_score":    m.get("min_match", ""),
        "score_std_dev":      m.get("score_std", ""),
        "avg_hire_probability": m.get("avg_hire_prob", ""),
        "high_match_pct":     m.get("high_match_pct", ""),
        "score_80_84":        dist.get("80-84", ""),
        "score_85_89":        dist.get("85-89", ""),
        "score_90_94":        dist.get("90-94", ""),
        "score_95_99":        dist.get("95-99", ""),
        "top_job_title":      top.get("title", ""),
        "top_job_company":    top.get("company", ""),
        "top_job_match":      top.get("match", ""),
        "top_job_hire_prob":  top.get("hire_prob", ""),
    }


FIELDNAMES = [
    "filename", "error",
    "experience_level", "years_experience", "education",
    "skills_count", "skills", "job_titles", "top_industries",
    "strengths", "gaps", "summary",
    "job_pool_size",
    "avg_match_score", "max_match_score", "min_match_score", "score_std_dev",
    "avg_hire_probability", "high_match_pct",
    "score_80_84", "score_85_89", "score_90_94", "score_95_99",
    "top_job_title", "top_job_company", "top_job_match", "top_job_hire_prob",
]


def main():
    parser = argparse.ArgumentParser(description="Batch-test resumes against JobFinder")
    parser.add_argument("--folder", required=True, help="Folder containing PDF resumes")
    parser.add_argument("--out",    default="batch_results.csv", help="Output CSV filename")
    parser.add_argument("--url",    default=BASE_URL, help="Base URL of the app (default: http://localhost:5000)")
    parser.add_argument("--delay",  type=float, default=DELAY, help="Seconds between batches (default: 1.5)")
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = args.url.rstrip("/")

    folder = Path(args.folder)
    if not folder.is_dir():
        sys.exit(f"Folder not found: {folder}")

    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {folder}")

    print(f"Found {len(pdfs)} PDFs in {folder}")
    print(f"Running in batches of {BATCH_SIZE}, writing to {args.out}\n")

    # Check server is up
    try:
        requests.get(f"{BASE_URL}/api/profile", timeout=5)
    except Exception:
        sys.exit(f"Cannot reach {BASE_URL} — is the app running?")

    session    = requests.Session()
    all_rows   = []
    batches    = [pdfs[i:i+BATCH_SIZE] for i in range(0, len(pdfs), BATCH_SIZE)]
    ok_count   = 0
    err_count  = 0

    for i, batch in enumerate(batches, 1):
        print(f"Batch {i}/{len(batches)}: {[p.name for p in batch]}")
        results = run_batch(session, batch)

        for r in results:
            row = flatten(r)
            all_rows.append(row)
            if r.get("error"):
                print(f"  ✗ {r['filename']}: {r['error']}")
                err_count += 1
            else:
                mm = r.get("match_metrics")
                score_str = f"  avg match={r['match_metrics']['avg_match']}%  hire={r['match_metrics']['avg_hire_prob']}%  std={r['match_metrics']['score_std']}" if mm else "  (no job pool — scores not computed)"
                print(f"  ✓ {r['filename']}{score_str}")
                ok_count += 1

        if i < len(batches):
            print(f"  Waiting {args.delay}s...\n")
            time.sleep(args.delay)

    # Write CSV
    out_path = Path(args.out)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nDone — {ok_count} parsed, {err_count} errors")
    print(f"Results written to: {out_path.resolve()}")

    # Quick summary stats if match scores exist
    scored = [r for r in all_rows if r.get("avg_match_score") != ""]
    if scored:
        scores = [float(r["avg_match_score"]) for r in scored]
        hps    = [float(r["avg_hire_probability"]) for r in scored]
        print(f"\nMatch score summary across {len(scored)} resumes:")
        print(f"  Mean:    {sum(scores)/len(scores):.1f}%")
        print(f"  Highest: {max(scores):.1f}%  ({scored[scores.index(max(scores))]['filename']})")
        print(f"  Lowest:  {min(scores):.1f}%  ({scored[scores.index(min(scores))]['filename']})")
        print(f"  Avg hire probability: {sum(hps)/len(hps):.1f}%")


if __name__ == "__main__":
    main()
