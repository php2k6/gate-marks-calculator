#!/usr/bin/env python3
"""
GATE Result Calculator – Web App
=================================
Run:  python app.py   (from inside web/)
Then open  http://127.0.0.1:5000

Security model
--------------
* Binds to 127.0.0.1 only – not reachable from other machines on the network.
* All file I/O happens inside  web/uploads/  using random UUIDs.
  The input HTML is deleted immediately after parsing.
* PDF lives in uploads/ until a signed, expiring, single-use token is redeemed.
* Token is generated with itsdangerous (comes with Flask) – HMAC-SHA256 signed,
  10-minute TTL, burned on first use.
* A used-token set tracks redemptions so a captured link can't be replayed.
* Stale upload files (> 15 min old) are swept on every request.
* No route serves answer_keys/, results/, responses/ or uploads/ as static dirs.
"""

import re, sys, csv, json, uuid, time, os
from pathlib import Path
from datetime import datetime

from flask import (Flask, render_template, request,
                   send_file, abort, url_for)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# ── path setup (web/ is self-contained) ─────────────────────────────────────
_WEB = Path(__file__).parent
sys.path.insert(0, str(_WEB))

from response_parser import load_html, parse_info, parse_responses
from calculate_result import (
    load_answer_key, score_question, type_metrics, generate_pdf_report
)
from bs4 import BeautifulSoup

UPLOADS     = _WEB / "uploads"
QUERIES_CSV = _WEB / "queries.csv"
UPLOADS.mkdir(exist_ok=True)

# ── app & token signer ────────────────────────────────────────────────────────
SECRET   = os.environ.get("GATE_SECRET", "gate-result-local-secret-2026-change-me")
app      = Flask(__name__)
app.secret_key = SECRET
_signer  = URLSafeTimedSerializer(SECRET, salt="pdf-download")

# In-memory set of already-redeemed tokens (prevents replay within process lifetime)
_used_tokens: set[str] = set()

TOKEN_TTL = 600  # seconds – 10 minutes


# ── query logger ──────────────────────────────────────────────────────────
QUERY_FIELDS = [
    "timestamp", "paper", "shift", "date", "url",
    "candidate_id", "candidate_name",
    "total_marks", "max_marks", "correct", "incorrect", "not_attempted",
    "status", "error",
]

def _log_query(**kwargs):
    """Append one row to queries.csv, creating header if file is new."""
    row = {f: kwargs.get(f, "") for f in QUERY_FIELDS}
    row["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_header = not QUERIES_CSV.exists() or QUERIES_CSV.stat().st_size == 0
    with open(QUERIES_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUERY_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(row)


# ── stale-file sweeper ───────────────────────────────────────────────────────
def _sweep_uploads(max_age: int = 900):
    """Delete files in uploads/ and its subdirs older than max_age seconds."""
    now = time.time()
    for p in UPLOADS.rglob("*"):
        if p.is_file():
            try:
                if now - p.stat().st_mtime > max_age:
                    p.unlink()
            except Exception:
                pass


# ── block direct access to sensitive dirs ────────────────────────────────────
@app.before_request
def _guard():
    _sweep_uploads()
    blocked = ("/uploads/", "/answer_keys/", "/results/", "/responses/")
    path = request.path
    if any(path.startswith(b) for b in blocked):
        abort(403)


# ── helpers ───────────────────────────────────────────────────────────────────
def _available_keys() -> list[dict]:
    keys = []
    pattern = re.compile(
        r"^(?P<paper>[A-Z]+)_shift(?P<shift>[^_]+)_(?P<date>[\d-]+)_answer_key\.csv$"
    )
    ak_dir = _WEB / "answer_keys"
    if not ak_dir.exists():
        return keys
    for f in sorted(ak_dir.glob("*.csv")):
        m = pattern.match(f.name)
        if m:
            keys.append({"paper": m["paper"], "shift": m["shift"],
                         "date": m["date"], "file": f.name})
    return keys


def _key_file(paper, shift, date) -> Path | None:
    shift_safe = re.sub(r"[^\w-]", "_", shift)
    date_safe  = re.sub(r"[^\w-]", "-", date)
    p = _WEB / "answer_keys" / f"{paper}_shift{shift_safe}_{date_safe}_answer_key.csv"
    return p if p.exists() else None


def _cleanup(name: str):
    """Remove uploads/pdf|json|csv/<name>.* and any loose uploads/<name>* files."""
    for sub in ("pdf", "json", "csv"):
        subdir = UPLOADS / sub
        if subdir.exists():
            for p in subdir.glob(f"{name}.*"):
                try: p.unlink()
                except Exception: pass
    for p in UPLOADS.glob(f"{name}*"):
        try: p.unlink()
        except Exception: pass


def _validate_url(url: str) -> str | None:
    url = url.strip()
    if not url:
        return "URL cannot be empty."
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return "Only http:// or https:// URLs are accepted."
    if len(url) > 2048:
        return "URL is too long."
    return None


def _index_ctx():
    keys    = _available_keys()
    cascade = {}
    for k in keys:
        cascade.setdefault(k["paper"], {}).setdefault(k["shift"], []).append(k["date"])
    return keys, sorted(cascade.keys()), cascade


def _index_error(error, status=400):
    keys, papers, cascade = _index_ctx()
    return render_template("index.html", keys=keys, papers=papers,
                           cascade_json=json.dumps(cascade), error=error), status



# ── routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    keys, papers, cascade = _index_ctx()
    return render_template("index.html", keys=keys, papers=papers,
                           cascade_json=json.dumps(cascade))


@app.route("/calculate", methods=["POST"])
def calculate():
    uid       = uuid.uuid4().hex

    try:
        # ── 1. validate inputs ────────────────────────────────────────────────
        paper = request.form.get("paper", "").strip().upper()
        shift = request.form.get("shift", "").strip()
        date  = request.form.get("date",  "").strip()
        url   = request.form.get("url",   "").strip()

        if not paper or not shift or not date:
            return _index_error("Please select paper, shift and date.")

        url_err = _validate_url(url)
        if url_err:
            return _index_error(url_err)

        # ── 2. check answer key exists ────────────────────────────────────────
        ak_path = _key_file(paper, shift, date)
        if not ak_path:
            return _index_error(
                f"Answer key for {paper} Shift-{shift} ({date}) has not been "
                f"parsed yet. Run answer_parser.py first.", 404)

        # ── 3. fetch & store response HTML (deleted after parse) ──────────────
        html_path = UPLOADS / f"{uid}.html"
        try:
            html = load_html(url)
        except SystemExit as e:
            return _index_error(f"Could not fetch the URL: {e}")

        html_path.write_text(html, encoding="utf-8", errors="replace")

        # ── 4. parse responses (read back from file for encoding consistency) ──
        html_clean = html_path.read_text(encoding="utf-8", errors="replace")
        soup       = BeautifulSoup(html_clean, "html.parser")
        info       = parse_info(soup)
        responses  = parse_responses(soup)

        cid   = info.get("Candidate ID",   "UNKNOWN").strip()
        cname = info.get("Candidate Name", "").strip()

        # HTML no longer needed
        html_path.unlink(missing_ok=True)

        # ── debug: log parsing summary to console ───────────────────────────
        from collections import Counter
        type_counts = Counter(r["q_type"] for r in responses)
        chosen_empty = sum(1 for r in responses
                           if r["q_type"] in ("MCQ","MSQ") and not r["chosen_option"])
        print(f"  [DEBUG] Parsed {len(responses)} responses: {dict(type_counts)}")
        print(f"  [DEBUG] MCQ/MSQ with empty chosen: {chosen_empty}")
        if responses:
            for r in responses[:3]:
                print(f"  [DEBUG] sample: {r}")

        if not responses:
            _cleanup(uid)
            return _index_error(
                "No question responses found in the page. "
                "Make sure the URL points to your personal GATE response sheet "
                "(not the answer-key PDF).", 422)

        # ── 5. score ──────────────────────────────────────────────────────────
        answer_key = load_answer_key(ak_path)
        ak_map     = {row["serial_no"]: row for row in answer_key}

        results   = []
        unmatched = 0
        for resp in responses:
            qid = resp["question_id"]
            if qid not in ak_map:
                unmatched += 1
                continue
            results.append(score_question(ak_map[qid], resp))

        if not results:
            _cleanup(out_base if 'out_base' in dir() else uid)
            return _index_error(
                "None of the questions matched the answer key. "
                "Double-check paper / shift / date selection.", 422)

        total     = round(sum(r["marks_awarded"] for r in results), 2)
        max_marks = round(sum(float(row["marks_correct"]) for row in answer_key), 2)
        correct   = sum(1 for r in results if r["status"] == "correct")
        incorrect = sum(1 for r in results if r["status"] == "incorrect")
        not_att   = sum(1 for r in results if r["status"] == "not_attempted")
        pct       = round(total / max_marks * 100, 1) if max_marks else 0

        type_stats = {qt: type_metrics(results, qt)
                      for qt in ["MCQ", "MSQ", "NAT"]
                      if any(r["q_type"] == qt for r in results)}

        sections  = sorted({r["section"] for r in results if r.get("section")})
        sec_stats = []
        for sec in sections:
            sr  = [r for r in results if r.get("section") == sec]
            sec_stats.append({
                "section":      sec,
                "total":        len(sr),
                "correct":      sum(1 for r in sr if r["status"] == "correct"),
                "incorrect":    sum(1 for r in sr if r["status"] == "incorrect"),
                "not_attempted":sum(1 for r in sr if r["status"] == "not_attempted"),
                "marks":        round(sum(r["marks_awarded"] for r in sr), 2),
            })

        # ── 6. build canonical output base name ────────────────────────────────
        cname_safe = re.sub(r"[^\w-]", "-", cname).strip("-") or cid
        out_base   = f"{cid}_{cname_safe}_result"   # e.g. CS26S32007046_PRABHAV-PATEL_result

        # ── 7. save JSON + CSV into uploads/json/ and uploads/csv/ ─────────────
        dir_json = UPLOADS / "json"
        dir_csv  = UPLOADS / "csv"
        dir_pdf  = UPLOADS / "pdf"
        for d in (dir_json, dir_csv, dir_pdf):
            d.mkdir(parents=True, exist_ok=True)

        report_data = {
            "candidate_id":   cid,
            "candidate_name": cname,
            "paper":          paper,
            "shift":          shift,
            "date":           date,
            "total_marks":    total,
            "max_marks":      max_marks,
            "correct":        correct,
            "incorrect":      incorrect,
            "not_attempted":  not_att,
            "by_type":        type_stats,
            "by_section":     sec_stats,
            "details":        results,
        }
        import json as _json, csv as _csv
        with open(dir_json / f"{out_base}.json", "w", encoding="utf-8") as _f:
            _json.dump(report_data, _f, indent=2)
        if results:
            with open(dir_csv / f"{out_base}.csv", "w", newline="", encoding="utf-8") as _f:
                _w = _csv.DictWriter(_f, fieldnames=results[0].keys())
                _w.writeheader()
                _w.writerows(results)

        # ── 8. generate PDF → uploads/pdf/<out_base>.pdf ─────────────────────
        pdf_path = dir_pdf / f"{out_base}.pdf"
        generate_pdf_report(
            pdf_path,
            {"id": cid, "name": cname, "paper": paper, "shift": shift, "date": date},
            {"total": total, "max_marks": max_marks, "correct": correct,
             "incorrect": incorrect, "not_attempted": not_att, "total_qs": len(results)},
            type_stats, results,
        )

        # ── 9. issue a signed, single-use download token ──────────────────────
        token  = _signer.dumps({"fname": f"{out_base}.pdf"})
        dl_url = url_for("download_pdf", token=token, _external=False)
        _log_query(
            paper=paper, shift=shift, date=date, url=url,
            candidate_id=cid, candidate_name=cname,
            total_marks=total, max_marks=max_marks,
            correct=correct, incorrect=incorrect, not_attempted=not_att,
            status="success",
        )
        warn = (f"⚠ {unmatched} question(s) couldn't be matched to the answer key."
                if unmatched else None)

        return render_template("result.html",
                               cid=cid, cname=cname,
                               paper=paper, shift=shift, date=date,
                               total=total, max_marks=max_marks, pct=pct,
                               correct=correct, incorrect=incorrect,
                               not_attempted=not_att,
                               type_stats=type_stats,
                               sec_stats=sec_stats,
                               results=results,
                               warning=warn,
                               dl_url=dl_url,
                               has_pdf=pdf_path.exists())

    except Exception as exc:
        _cleanup(out_base if 'out_base' in dir() else uid)
        app.logger.exception("Unexpected error during calculation")
        _log_query(
            paper=request.form.get("paper", ""),
            shift=request.form.get("shift", ""),
            date=request.form.get("date",  ""),
            url=request.form.get("url",   ""),
            status="error", error=str(exc),
        )
        return _index_error(f"An unexpected error occurred: {exc}", 500)


@app.route("/download-pdf")
def download_pdf():
    """
    Single-use, signed, time-limited PDF download.
    - Token must be valid HMAC signature (itsdangerous)
    - Token must not have been used before (in-memory set)
    - File is deleted from disk immediately after streaming
    """
    token = request.args.get("token", "")
    if not token:
        abort(403)

    # Verify signature + expiry
    try:
        payload = _signer.loads(token, max_age=TOKEN_TTL)
    except SignatureExpired:
        abort(410)   # Gone – link expired
    except BadSignature:
        abort(403)

    # Burn the token so it can't be used again
    if token in _used_tokens:
        abort(410)
    _used_tokens.add(token)
    # Prevent unbounded growth – trim old entries periodically
    if len(_used_tokens) > 10_000:
        _used_tokens.clear()

    fname    = payload.get("fname", "")
    pdf_path = UPLOADS / "pdf" / fname

    # Validate: plain filename only, no path separators, must end in .pdf
    if not fname or "/" in fname or "\\" in fname or not fname.endswith(".pdf"):
        abort(403)

    if not pdf_path.exists():
        abort(404)

    resp = send_file(pdf_path, as_attachment=True, download_name=fname,
                     mimetype="application/pdf")

    @resp.call_on_close
    def _delete_after_send():
        try:
            pdf_path.unlink(missing_ok=True)
        except Exception:
            pass

    return resp


# ── error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(403)
def err_403(e):
    return render_template("error.html", code=403, icon="🔒",
        title="Access Denied",
        message="You don't have permission to access this resource."), 403

@app.errorhandler(404)
def err_404(e):
    return render_template("error.html", code=404, icon="🔍",
        title="Not Found",
        message="The page or file you're looking for doesn't exist."), 404

@app.errorhandler(410)
def err_410(e):
    return render_template("error.html", code=410, icon="⏱",
        title="Download Link Expired or Already Used",
        message="This PDF download link has either expired (links last 10 minutes) "
                "or has already been used. Please re-submit the form to generate a "
                "new result and download link."), 410


if __name__ == "__main__":
    # 127.0.0.1 → only your machine can reach it
    app.run(host="127.0.0.1", port=5000, debug=True)
