# GATE Result Calculator

A Flask web app that lets GATE candidates instantly check their score by pasting their official GATE response-sheet URL. It fetches the response sheet, matches it against a pre-parsed answer key, and generates a detailed PDF report.

---

## Table of Contents

1. [Features](#features)
2. [Project Structure](#project-structure)
3. [Prerequisites](#prerequisites)
4. [Setup & Run](#setup--run)
5. [Admin Guide — Parsing the Answer Key](#admin-guide--parsing-the-answer-key)
   - [Step 1 — Get the Official Answer Key PDF](#step-1--get-the-official-answer-key-pdf)
   - [Step 2 — Find the Starting Serial Number](#step-2--find-the-starting-serial-number)
   - [Step 3 — Run answer_parser.py](#step-3--run-answer_parserpy)
   - [Answer Key File Naming Convention](#answer-key-file-naming-convention)
6. [How Candidates Use the Website](#how-candidates-use-the-website)
7. [Scoring Logic](#scoring-logic)
8. [Adding Support for New Papers / Shifts](#adding-support-for-new-papers--shifts)

---

## Features

- Paste your official GATE response-sheet URL — no file upload needed
- Cascading paper → shift → date selector built automatically from available answer keys
- Detailed PDF report with per-question breakdown, section-wise and type-wise (MCQ/MSQ/NAT) stats
- Admin query log written to `queries.csv`

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

## Setup & Run

```bash
# 1. Clone the repository
git clone https://github.com/php2k6/gate-marks-calculator.git
cd gate-marks-calculator

# 2. Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Parse at least one answer key (see Admin Guide below)

# 5. Start the server
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

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

## License

This project is released for educational / non-commercial use. Results are **indicative only** — always refer to the official GATE scorecard.
