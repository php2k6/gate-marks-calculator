# GATE Result Calculator

A self-hosted Flask web application that lets GATE candidates instantly check their score by pasting their official GATE response-sheet URL. The app fetches the response sheet, matches it against a pre-parsed answer key, and generates a detailed PDF report — all without storing any personal data beyond the upload session (files are auto-deleted after 15 minutes).

---

## Table of Contents

1. [Features](#features)
2. [Project Structure](#project-structure)
3. [Prerequisites](#prerequisites)
4. [Local Deployment](#local-deployment)
5. [Production Deployment](#production-deployment)
6. [Admin Guide — Parsing the Answer Key](#admin-guide--parsing-the-answer-key)
   - [Step 1 — Get the Official Answer Key PDF](#step-1--get-the-official-answer-key-pdf)
   - [Step 2 — Find the Starting Serial Number](#step-2--find-the-starting-serial-number)
   - [Step 3 — Run answer_parser.py](#step-3--run-answer_parserpy)
   - [Answer Key File Naming Convention](#answer-key-file-naming-convention)
7. [How Candidates Use the Website](#how-candidates-use-the-website)
8. [Security Model](#security-model)
9. [Scoring Logic](#scoring-logic)
10. [Adding Support for New Papers / Shifts](#adding-support-for-new-papers--shifts)
11. [Environment Variables](#environment-variables)
12. [Troubleshooting](#troubleshooting)

---

## Features

- **Instant GATE score** computed from the official GATE CDN response URL — no file upload needed by the candidate
- **Cascading paper → shift → date** selector automatically built from the answer keys present on disk
- **PDF report** with a per-question breakdown, section-wise stats, and MCQ / MSQ / NAT metrics; downloaded via a signed single-use link
- **JSON + CSV** intermediate results saved in `uploads/` for audit
- **Admin query log** written to `queries.csv`
- **Auto-cleanup** — all temporary files older than 15 min are purged on every request
- **Security** — 10-min signed PDF tokens, no direct static serving of sensitive directories, binds to `127.0.0.1` by default

---

## Project Structure

```
web/
├── app.py               # Flask application & routes
├── answer_parser.py     # Admin tool: converts official answer-key PDF → CSV/JSON
├── response_parser.py   # Fetches & parses GATE response-sheet HTML
├── calculate_result.py  # Scoring engine + PDF report generator
├── queries.csv          # Auto-generated log of every calculation request
│
├── answer_keys/         # PRE-PARSED answer keys (CSV + JSON)
│   └── <PAPER>_shift<N>_<YYYY-MM-DD>_answer_key.csv
│   └── <PAPER>_shift<N>_<YYYY-MM-DD>_answer_key.json
│
├── uploads/             # Temporary session files (auto-deleted)
│   ├── csv/
│   ├── json/
│   └── pdf/
│
├── static/
│   └── style.css
│
└── templates/
    ├── base.html
    ├── index.html
    ├── result.html
    └── error.html
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10 + |
| pip | latest |

Install Python dependencies:

```bash
pip install flask itsdangerous beautifulsoup4 pdfplumber reportlab
```

Or install from a requirements file if provided:

```bash
pip install -r requirements.txt
```

---

## Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/php2k6/gate-marks-calculator.git
cd gate-marks-calculator

# 2. (Recommended) Create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 3. Install dependencies
pip install flask itsdangerous beautifulsoup4 pdfplumber reportlab

# 4. Parse at least one answer key (see Admin Guide below)

# 5. Start the server
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

> **Note:** By default `app.py` binds to `127.0.0.1` only. To expose it on your local network during testing replace the last line with `app.run(host="0.0.0.0", port=5000, debug=False)`.

---

## Production Deployment

### Option A — Gunicorn + Nginx (Linux / VPS)

```bash
pip install gunicorn

# Run Gunicorn bound to a local socket
gunicorn -w 4 -b 127.0.0.1:8000 app:app
```

Minimal Nginx config (`/etc/nginx/sites-available/gate-calc`):

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        client_max_body_size 5M;
    }
}
```

Enable & restart:

```bash
sudo ln -s /etc/nginx/sites-available/gate-calc /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl restart nginx
```

Add HTTPS with Certbot:

```bash
sudo certbot --nginx -d your-domain.com
```

### Option B — Systemd Service (keep it running)

Create `/etc/systemd/system/gate-calc.service`:

```ini
[Unit]
Description=GATE Result Calculator
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/gate-marks-calculator
Environment="GATE_SECRET=replace-with-a-strong-random-secret"
ExecStart=/var/www/gate-marks-calculator/.venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gate-calc
```

### Option C — Docker (quick deploy anywhere)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir flask itsdangerous beautifulsoup4 pdfplumber reportlab gunicorn
EXPOSE 8000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]
```

```bash
docker build -t gate-calc .
docker run -d -p 80:8000 -e GATE_SECRET="your-secret" gate-calc
```

---

## Admin Guide — Parsing the Answer Key

Before candidates can use the website, an admin must convert the official GATE answer-key PDF into the machine-readable CSV format the app expects. This is done with **`answer_parser.py`**.

### Step 1 — Get the Official Answer Key PDF

Download the official GATE answer key PDF from the GATE portal (usually published a few days after each exam session). Save it somewhere accessible, e.g. `C:\gate\CS_shift1_answerkey.pdf`.

### Step 2 — Find the Starting Serial Number

This is the most important step. GATE assigns each question a **unique numeric Question ID** (also called `question_id` or `serial_no`). These IDs appear in the candidate's personal response HTML. The answer key PDF only lists question numbers 1–65; it does not directly list these IDs.

**How to find it:**

1. Open any candidate's GATE response sheet HTML (view in browser or open source).
2. Look for the `Question ID` field in any question panel. It will be a large number like `22848211137`.
3. Identify which question number (Q1, Q2, …) that ID corresponds to by cross-referencing the **master question paper** (the version-agnostic question booklet PDF that GATE publishes). Q1 (first General Aptitude question) always has the lowest ID in the set.
4. The ID shown for **Q1** is your **starting serial number**.

> **Example:** If Q1's `Question ID` is `22848211137`, then Q2 = `22848211138`, Q3 = `22848211139`, … Q65 = `22848211201`. Each question gets the next consecutive integer. `answer_parser.py` assigns these automatically once you provide the starting number.

**Visual walkthrough (response sheet HTML):**

```
┌──────────────────────────────────────────┐
│  Question Type : MCQ                     │
│  Question ID   : 22848211137   ◄── this  │
│  Status        : Answered                │
│  Chosen Option : B                       │
└──────────────────────────────────────────┘
```

Find the panel for Q1 of the paper (Section = GA, Q No = 1) and read its `Question ID`.

### Step 3 — Run answer_parser.py

```bash
python answer_parser.py
```

The script will interactively ask for:

| Prompt | Example value | Notes |
|---|---|---|
| PDF path | `C:\gate\CS_shift1_answerkey.pdf` | Path to the official answer-key PDF |
| Starting serial number | `22848211137` | The Question ID of Q1 from Step 2 |
| Paper type | `CS` or `DA` | The GATE paper code |
| Shift | `1` | Use `1`, `2`, `morning`, `afternoon`, etc. |
| Date | `2026-02-08` | Format `YYYY-MM-DD` |

You can also pass the PDF path and starting serial directly:

```bash
python answer_parser.py "C:\gate\CS_shift1_answerkey.pdf" 22848211137
```

The script will then prompt for paper type, shift, and date interactively.

**What the script does internally:**

1. Opens the PDF with `pdfplumber` and reads each line matching the pattern  
   `<Q_No>  <MCQ|MSQ|NAT>  <Section>  <Answer>`.
2. For each question, assigns `serial_no = starting_serial + (question_index)`.
3. Applies the GATE marking scheme:
   - Q1–Q5: 1 mark (GA)
   - Q6–Q10: 2 marks (GA)
   - Q11–Q35: 1 mark (Core)
   - Q36–Q65: 2 marks (Core)
   - MCQ negative: −mark/3 | MSQ / NAT negative: 0
4. Saves `answer_keys/<PAPER>_shift<SHIFT>_<DATE>_answer_key.csv` and `.json`.

### Answer Key File Naming Convention

```
answer_keys/<PAPER>_shift<SHIFT>_<DATE>_answer_key.csv
```

Examples:
```
answer_keys/CS_shift1_2026-02-08_answer_key.csv
answer_keys/CS_shift2_2026-02-08_answer_key.csv
answer_keys/DA_shift2_2026-02-15_answer_key.csv
```

The web app auto-discovers all `*.csv` files in `answer_keys/` at startup and populates the paper/shift/date dropdowns accordingly.

### Answer Key CSV Format

```csv
serial_no,q_no,q_type,section,answer,marks_correct,marks_incorrect
22848211137,1,MCQ,GA,B,1,-0.3333
22848211138,2,MCQ,GA,C,1,-0.3333
...
22848211201,65,NAT,Core,3 to 3,2,0.0
```

---

## How Candidates Use the Website

1. Log in to **goaps.iitr.ac.in** → **View Responses** → copy the URL from the browser address bar (it points to a CDN HTML page like `https://cdn.digialm.com//…/YourID_GATEpaper.html`).
2. Open the GATE Result Calculator website.
3. Paste the URL into the **Response Sheet URL** field.
4. Select **Paper**, **Shift**, and **Exam Date** from the cascading dropdowns.
5. Click **Calculate My Score**.
6. View the detailed score breakdown (by type: MCQ/MSQ/NAT, by section: GA/Core).
7. Download the **PDF report** using the signed link (valid for 10 minutes, single-use).

---

## Security Model

| Feature | Detail |
|---|---|
| Local-only binding | Default `127.0.0.1:5000` — not reachable from outside |
| PDF download tokens | HMAC-SHA256 signed (itsdangerous), 10-min TTL, single-use burn |
| Token replay prevention | In-memory used-token set rejects replayed links within process lifetime |
| Stale file sweep | All `uploads/` files older than 15 min are deleted on every request |
| Blocked static dirs | `/uploads/`, `/answer_keys/`, `/results/`, `/responses/` return 403 |
| No persistent PII | Response HTML deleted immediately after parsing; PDF deleted after download |
| UUID file names | All temp files use random UUID names — no predictable paths |
| Secret key | Set via `GATE_SECRET` env var — change the default before going to production |

---

## Scoring Logic

| Question Type | Correct | Incorrect | Not Attempted |
|---|---|---|---|
| MCQ | +marks | −marks/3 | 0 |
| MSQ | +marks | 0 | 0 |
| NAT | +marks | 0 | 0 |

Marks per question follow the official GATE scheme:

| Range | Marks |
|---|---|
| Q1–Q5 (GA) | 1 |
| Q6–Q10 (GA) | 2 |
| Q11–Q35 (Core) | 1 |
| Q36–Q65 (Core) | 2 |

MSQ answers are matched as sets (order-independent). NAT answers are checked against the official range `lo to hi` (floating-point inclusive).

---

## Adding Support for New Papers / Shifts

1. Download the new exam's official answer-key PDF from the GATE portal.
2. Find the starting serial number (Q1's `Question ID`) from any candidate's response sheet for that session (see [Step 2](#step-2--find-the-starting-serial-number)).
3. Run `answer_parser.py` with the correct PDF, starting serial, paper type, shift, and date.
4. The new key files appear in `answer_keys/` and the dropdowns update automatically on next page load — no server restart required.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GATE_SECRET` | `gate-result-local-secret-2026-change-me` | HMAC secret for signing PDF download tokens. **Must be changed in production.** |

Set it before starting the server:

```bash
# Linux / macOS
export GATE_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py

# Windows PowerShell
$env:GATE_SECRET = (python -c "import secrets; print(secrets.token_hex(32))")
python app.py
```

---

## Troubleshooting

### "No question responses found in the page"
- Make sure the URL is your **personal response sheet**, not the official answer-key PDF.
- The URL should look like `https://cdn.digialm.com/…/YourCandidateID_GATE….html`.
- Try opening the URL in a browser first to confirm it loads.

### "Answer key for X has not been parsed yet"
- Run `answer_parser.py` for the correct paper/shift/date combination (see Admin Guide).
- File names are case-sensitive. Verify the file exists in `answer_keys/`.

### "None of the questions matched the answer key"
- The **starting serial number** used during `answer_parser.py` was wrong.
- Open any response sheet for that session, read Q1's `Question ID`, and re-run `answer_parser.py` with the correct value.
- Also check that you selected the correct paper, shift, and date in the dropdown.

### PDF download link expired / already used
- The signed link is valid for **10 minutes** and can only be used **once**.
- Re-submit the form to generate a fresh result and a new download link.

### `pdfplumber` cannot read the answer key PDF
- Some GATE PDFs use scanned images. In that case, OCR the PDF first (e.g. with `ocrmypdf`) before passing it to `answer_parser.py`.
- Ensure the PDF rows follow the pattern: `<Q_No> <MCQ|MSQ|NAT> <Section> <Answer>`.

---

## License

This project is released for educational / non-commercial use. Results are **indicative only** — always refer to the official GATE scorecard.
