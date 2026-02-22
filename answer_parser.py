#!/usr/bin/env python3
"""
GATE Answer Key -> JSON/CSV mapper
Usage:
    python gate_grader.py <pdf_path> <starting_serial>

Example:
    python gate_grader.py answerKey.pdf 22848211137

Marking scheme
--------------
  Q  1 -  5  : 1 mark    (GA)
  Q  6 - 10  : 2 marks   (GA)
  Q 11 - 35  : 1 mark    (Core)
  Q 36 - 65  : 2 marks   (Core)

  MCQ  : +x correct,  -x/3 incorrect
  MSQ  : +x correct,   0   incorrect
  NAT  : +x correct,   0   incorrect
"""

import sys, json, csv, re
from pathlib import Path
import pdfplumber

# -- marks per question range -------------------------------------------------
def pos_marks(q: int) -> int:
    if   1  <= q <= 5:  return 1
    elif 6  <= q <= 10: return 2
    elif 11 <= q <= 35: return 1
    elif 36 <= q <= 65: return 2
    return 1  # fallback

def neg_marks(q: int, qtype: str) -> float:
    if qtype == "MCQ":
        return round(-pos_marks(q) / 3, 4)
    return 0.0  # MSQ and NAT have no negative marking

# -- PDF parser ---------------------------------------------------------------
ROW = re.compile(r"^(\d+)\s+(MCQ|MSQ|NAT)\s+(\S+)\s+(.+)$", re.IGNORECASE)

def parse_pdf(pdf_path: str) -> list:
    rows = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                m = ROW.match(line.strip())
                if not m:
                    continue
                qno   = int(m.group(1))
                qtype = m.group(2).upper()
                rows[qno] = {
                    "q_no":    qno,
                    "q_type":  qtype,
                    "section": m.group(3),
                    "answer":  m.group(4).strip(),
                }
    return [rows[k] for k in sorted(rows)]

# -- helpers -----------------------------------------------------------------
def prompt_choice(prompt: str, choices: list[str]) -> str:
    """Ask user to pick from a list; keeps asking until valid input."""
    choices_lower = [c.lower() for c in choices]
    while True:
        val = input(f"{prompt} [{'/'.join(choices)}]: ").strip()
        if val.lower() in choices_lower:
            return choices[choices_lower.index(val.lower())]
        print(f"  Please enter one of: {', '.join(choices)}")

def prompt_text(prompt: str) -> str:
    while True:
        val = input(f"{prompt}: ").strip()
        if val:
            return val
        print("  Value cannot be empty.")

# -- main ---------------------------------------------------------------------
def main():
    if len(sys.argv) == 3:
        pdf_path = sys.argv[1]
        start    = int(sys.argv[2])
    elif len(sys.argv) == 1:
        pdf_path = prompt_text("PDF path")
        start    = int(prompt_text("Starting serial number"))
    else:
        sys.exit("Usage: python answer_parser.py [<pdf_path> <starting_serial>]")

    # Collect paper metadata
    print()
    paper_type = prompt_choice("Paper type", ["CS", "DA"])
    shift      = prompt_text("Shift (e.g. 1, 2, morning, afternoon)")
    date       = prompt_text("Date (e.g. 2026-02-15)")

    # Sanitise shift/date for use in filenames
    shift_safe = re.sub(r"[^\w-]", "_", shift)
    date_safe  = re.sub(r"[^\w-]", "-", date)
    base_name  = f"{paper_type}_shift{shift_safe}_{date_safe}"
    print()

    print(f"Parsing {pdf_path} ...")
    questions = parse_pdf(pdf_path)
    print(f"  Found {len(questions)} questions")

    records = []
    for i, q in enumerate(questions):
        qno   = q["q_no"]
        qtype = q["q_type"]
        pm    = pos_marks(qno)
        nm    = neg_marks(qno, qtype)
        records.append({
            "serial_no":       start + i,
            "q_no":            qno,
            "q_type":          qtype,
            "section":         q["section"],
            "answer":          q["answer"],
            "marks_correct":   pm,
            "marks_incorrect": nm,
        })

    out_dir = Path(__file__).parent / "answer_keys"
    out_dir.mkdir(exist_ok=True)

    json_file = out_dir / f"{base_name}_answer_key.json"
    csv_file  = out_dir / f"{base_name}_answer_key.csv"

    # JSON
    with open(json_file, "w") as f:
        json.dump(records, f, indent=2)
    print(f"Saved -> {json_file}")

    # CSV
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(f"Saved -> {csv_file}")

if __name__ == "__main__":
    main()
