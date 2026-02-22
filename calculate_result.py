#!/usr/bin/env python3
"""
GATE Result Calculator
======================
Asks for paper type / shift / date  →  locates the pre-parsed answer key
Asks for the candidate response URL/path  →  parses it on the fly
Matches question_id (response) == serial_no (answer key) and scores marks.

Usage:
    python calculate_result.py          # fully interactive
"""

import re, json, csv, sys
from pathlib import Path
from datetime import datetime

# -- reuse parsers from sibling scripts --------------------------------------
# (they must live in the same directory)
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

try:
    from response_parser import load_html, parse_info, parse_responses
except ImportError as e:
    sys.exit(f"Cannot import response_parser.py: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def prompt_choice(prompt: str, choices: list) -> str:
    choices_lower = [c.lower() for c in choices]
    while True:
        val = input(f"{prompt} [{'/'.join(choices)}]: ").strip()
        if val.lower() in choices_lower:
            return choices[choices_lower.index(val.lower())]
        print(f"  Enter one of: {', '.join(choices)}")

def prompt_text(prompt: str) -> str:
    while True:
        val = input(f"{prompt}: ").strip()
        if val:
            return val
        print("  Value cannot be empty.")


# ---------------------------------------------------------------------------
# NAT range check  ("3 to 3", "4.24 to 4.26")
# ---------------------------------------------------------------------------
def nat_correct(student: str, key_range: str) -> bool:
    try:
        val = float(student.strip())
    except ValueError:
        return False
    m = re.match(r"([\d.]+)\s+to\s+([\d.]+)", key_range.strip(), re.IGNORECASE)
    if not m:
        return False
    return float(m.group(1)) <= val <= float(m.group(2))


# ---------------------------------------------------------------------------
# Score one question
# ---------------------------------------------------------------------------
def score_question(ak: dict, resp: dict) -> dict:
    """
    ak   : row from answer key   {serial_no, q_no, q_type, answer,
                                   marks_correct, marks_incorrect, ...}
    resp : row from response     {question_id, q_type, status, chosen_option}
    """
    q_type   = ak["q_type"].upper()
    key      = ak["answer"].strip()
    given    = resp["chosen_option"].strip()
    pos      = float(ak["marks_correct"])
    neg      = float(ak["marks_incorrect"])

    if not given:
        status  = "not_attempted"
        awarded = 0.0
    elif q_type == "MCQ":
        if given.upper() == key.upper():
            status, awarded = "correct", pos
        else:
            status, awarded = "incorrect", neg
    elif q_type == "MSQ":
        student_set = {x.strip().upper() for x in given.split(";")}
        key_set     = {x.strip().upper() for x in key.split(";")}
        if student_set == key_set:
            status, awarded = "correct", pos
        else:
            status, awarded = "incorrect", 0.0
    else:  # NAT
        if nat_correct(given, key):
            status, awarded = "correct", pos
        else:
            status, awarded = "incorrect", 0.0

    return {
        "serial_no":      ak["serial_no"],
        "q_no":           ak["q_no"],
        "q_type":         q_type,
        "section":        ak["section"],
        "correct_answer": key,
        "given_answer":   given,
        "status":         status,
        "marks_awarded":  round(awarded, 4),
    }


# ---------------------------------------------------------------------------
# Load answer key CSV
# ---------------------------------------------------------------------------
def load_answer_key(csv_path: Path) -> list:
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Per-type metrics helper
# ---------------------------------------------------------------------------
def type_metrics(results: list, q_type: str) -> dict:
    rows = [r for r in results if r["q_type"] == q_type]
    correct   = sum(1 for r in rows if r["status"] == "correct")
    incorrect = sum(1 for r in rows if r["status"] == "incorrect")
    not_att   = sum(1 for r in rows if r["status"] == "not_attempted")
    marks     = round(sum(r["marks_awarded"] for r in rows), 2)
    return {"total": len(rows), "correct": correct,
            "incorrect": incorrect, "not_attempted": not_att, "marks": marks}


# ---------------------------------------------------------------------------
# PDF Report
# ---------------------------------------------------------------------------
def generate_pdf_report(pdf_path, candidate: dict, summary: dict,
                        type_stats: dict, results: list):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, KeepTogether
        )
    except ImportError:
        print("  [PDF skipped] reportlab not installed. pip install reportlab")
        return

    # ── colours ────────────────────────────────────────────────────────────
    BLUE    = colors.HexColor("#1a3a6b")
    ORANGE  = colors.HexColor("#e87722")
    LGRAY   = colors.HexColor("#f4f6fa")
    MGRAY   = colors.HexColor("#d0d7e6")
    GREEN   = colors.HexColor("#1e7e34")
    RED     = colors.HexColor("#c0392b")
    AMBER   = colors.HexColor("#d68000")
    WHITE   = colors.white
    BLACK   = colors.black

    # ── styles ─────────────────────────────────────────────────────────────
    styles  = getSampleStyleSheet()
    H1      = ParagraphStyle("h1", fontSize=20, textColor=WHITE,
                             fontName="Helvetica-Bold", alignment=TA_CENTER,
                             spaceAfter=2)
    H2      = ParagraphStyle("h2", fontSize=11, textColor=WHITE,
                             fontName="Helvetica", alignment=TA_CENTER)
    LABEL   = ParagraphStyle("label", fontSize=9, textColor=BLUE,
                             fontName="Helvetica-Bold")
    BODY    = ParagraphStyle("body", fontSize=9, fontName="Helvetica")
    SECT    = ParagraphStyle("sect", fontSize=11, textColor=BLUE,
                             fontName="Helvetica-Bold", spaceBefore=12,
                             spaceAfter=4)
    FOOTER  = ParagraphStyle("footer", fontSize=7, textColor=colors.grey,
                             fontName="Helvetica", alignment=TA_CENTER)

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )
    W = A4[0] - 3.6*cm   # usable width
    story = []

    # ── banner header ──────────────────────────────────────────────────────
    banner_data = [[
        Paragraph("GATE Result Report", H1),
    ]]
    banner = Table(banner_data, colWidths=[W])
    banner.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,-1), BLUE),
        ("TOPPADDING",   (0,0), (-1,-1), 10),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("LINEBELOW",    (0,-1),(-1,-1), 4, ORANGE),
    ]))
    sub_data = [[
        Paragraph(f"Paper: {candidate['paper']}  |  "
                  f"Shift: {candidate['shift']}  |  "
                  f"Date: {candidate['date']}", H2)
    ]]
    sub = Table(sub_data, colWidths=[W])
    sub.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,-1), BLUE),
        ("BOTTOMPADDING",(0,0), (-1,-1), 10),
    ]))
    story += [banner, sub, Spacer(1, 0.35*cm)]

    # ── candidate info ─────────────────────────────────────────────────────
    story.append(Paragraph("Candidate Information", SECT))
    ci = [
        [Paragraph("Candidate ID",   LABEL), Paragraph(candidate['id'],   BODY),
         Paragraph("Candidate Name", LABEL), Paragraph(candidate['name'], BODY)],
    ]
    ci_tbl = Table(ci, colWidths=[W*0.18, W*0.32, W*0.18, W*0.32])
    ci_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,-1), LGRAY),
        ("BOX",          (0,0), (-1,-1), 0.5, MGRAY),
        ("INNERGRID",    (0,0), (-1,-1), 0.3, MGRAY),
        ("TOPPADDING",   (0,0), (-1,-1), 6),
        ("BOTTOMPADDING",(0,0), (-1,-1), 6),
        ("LEFTPADDING",  (0,0), (-1,-1), 8),
    ]))
    story += [ci_tbl, Spacer(1, 0.4*cm)]

    # ── score scorecard ────────────────────────────────────────────────────
    story.append(Paragraph("Score Summary", SECT))
    pct = round(summary['total'] / summary['max_marks'] * 100, 1) \
          if summary['max_marks'] else 0

    SC = ParagraphStyle("sc_num", fontSize=22, fontName="Helvetica-Bold",
                        alignment=TA_CENTER, textColor=BLUE)
    SC_LBL = ParagraphStyle("sc_lbl", fontSize=8, fontName="Helvetica",
                            alignment=TA_CENTER, textColor=colors.grey)
    def card_col(val, lbl, col):
        d = [[Paragraph(str(val), SC)], [Paragraph(lbl, SC_LBL)]]
        t = Table(d, colWidths=[W*0.24])
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0,0), (-1,-1), LGRAY),
            ("BOX",          (0,0), (-1,-1), 1.5, col),
            ("TOPPADDING",   (0,0), (-1,-1), 8),
            ("BOTTOMPADDING",(0,0), (-1,-1), 8),
        ]))
        return t

    scorecard_row = [[
        card_col(f"{summary['total']} / {summary['max_marks']}", "Total Score", BLUE),
        card_col(f"{pct}%",       "Percentage",     ORANGE),
        card_col(summary['correct'],   "Correct",   GREEN),
        card_col(summary['incorrect'],  "Incorrect", RED),
    ]]
    scorecard = Table(scorecard_row, colWidths=[W*0.25]*4,
                      hAlign="LEFT", spaceAfter=4)
    scorecard.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),
                                   ("RIGHTPADDING",(0,0),(-1,-1),6)]))
    story += [scorecard, Spacer(1, 0.4*cm)]

    # ── per-type breakdown ─────────────────────────────────────────────────
    story.append(Paragraph("Question Type Breakdown", SECT))
    HDR = ParagraphStyle("thdr", fontSize=9, fontName="Helvetica-Bold",
                         textColor=WHITE, alignment=TA_CENTER)
    CELL= ParagraphStyle("tcell", fontSize=9, fontName="Helvetica",
                         alignment=TA_CENTER)
    type_hdr = [[Paragraph(h, HDR) for h in
                 ["Type","Total Qs","Correct","Incorrect","Not Attempted","Marks"]]]
    type_rows = []
    for qt, st in type_stats.items():
        type_rows.append([Paragraph(qt, CELL),
                          Paragraph(str(st["total"]),        CELL),
                          Paragraph(str(st["correct"]),      CELL),
                          Paragraph(str(st["incorrect"]),    CELL),
                          Paragraph(str(st["not_attempted"]),CELL),
                          Paragraph(str(st["marks"]),        CELL)])
    total_row = [Paragraph(h, ParagraphStyle("tot", fontSize=9,
                fontName="Helvetica-Bold", alignment=TA_CENTER)) for h in [
        "TOTAL",
        str(summary["total_qs"]),
        str(summary["correct"]),
        str(summary["incorrect"]),
        str(summary["not_attempted"]),
        str(summary["total"]),
    ]]
    type_data = type_hdr + type_rows + [total_row]
    cw = [W*0.12, W*0.14, W*0.14, W*0.16, W*0.22, W*0.22]
    t2 = Table(type_data, colWidths=cw)
    t2_style = [
        ("BACKGROUND",   (0,0), (-1,0),  BLUE),
        ("BACKGROUND",   (0,-1),(-1,-1), MGRAY),
        ("INNERGRID",    (0,0), (-1,-1), 0.3, MGRAY),
        ("BOX",          (0,0), (-1,-1), 0.5, MGRAY),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
    ]
    for i in range(1, len(type_data)-1):
        if i % 2 == 0:
            t2_style.append(("BACKGROUND", (0,i), (-1,i), LGRAY))
    t2.setStyle(TableStyle(t2_style))
    story += [t2, Spacer(1, 0.4*cm)]

    # ── section breakdown ──────────────────────────────────────────────────
    sections = sorted({r["section"] for r in results if r.get("section")})
    if sections:
        story.append(Paragraph("Section Breakdown", SECT))
        sec_hdr = [[Paragraph(h, HDR) for h in
                    ["Section","Total Qs","Correct","Incorrect","Not Attempted","Marks"]]]
        sec_rows = []
        for sec in sections:
            sr = [r for r in results if r.get("section")==sec]
            sc_ = sum(1 for r in sr if r["status"]=="correct")
            si_ = sum(1 for r in sr if r["status"]=="incorrect")
            sna = sum(1 for r in sr if r["status"]=="not_attempted")
            sm  = round(sum(r["marks_awarded"] for r in sr), 2)
            sec_rows.append([Paragraph(sec, CELL),
                             Paragraph(str(len(sr)), CELL),
                             Paragraph(str(sc_), CELL),
                             Paragraph(str(si_), CELL),
                             Paragraph(str(sna), CELL),
                             Paragraph(str(sm),  CELL)])
        sec_data = sec_hdr + sec_rows
        t3 = Table(sec_data, colWidths=cw)
        t3_style = [
            ("BACKGROUND",   (0,0), (-1,0), BLUE),
            ("INNERGRID",    (0,0), (-1,-1), 0.3, MGRAY),
            ("BOX",          (0,0), (-1,-1), 0.5, MGRAY),
            ("TOPPADDING",   (0,0), (-1,-1), 5),
            ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ]
        for i in range(1, len(sec_data)):
            if i % 2 == 0:
                t3_style.append(("BACKGROUND", (0,i), (-1,i), LGRAY))
        t3.setStyle(TableStyle(t3_style))
        story += [t3, Spacer(1, 0.4*cm)]

    # ── detailed question table ────────────────────────────────────────────
    story.append(Paragraph("Detailed Question Results", SECT))
    det_hdr = [[Paragraph(h, HDR) for h in
                ["Q No","Type","Section","Correct Answer","Given Answer",
                 "Status","Marks"]]]
    STATUS_COL  = {"correct": GREEN, "incorrect": RED, "not_attempted": AMBER}
    det_rows = []
    for r in results:
        st_col = STATUS_COL.get(r["status"], BLACK)
        ST_CELL = ParagraphStyle("stc", fontSize=8, fontName="Helvetica-Bold",
                                 alignment=TA_CENTER, textColor=st_col)
        label = {"correct": "✔", "incorrect": "✘",
                 "not_attempted": "—"}.get(r["status"], r["status"])
        det_rows.append([
            Paragraph(r["q_no"],             CELL),
            Paragraph(r["q_type"],           CELL),
            Paragraph(r.get("section",""),   CELL),
            Paragraph(r["correct_answer"],   CELL),
            Paragraph(r["given_answer"] or "—", CELL),
            Paragraph(label, ST_CELL),
            Paragraph(str(r["marks_awarded"]), CELL),
        ])
    det_data = det_hdr + det_rows
    det_cw   = [W*0.09, W*0.08, W*0.16, W*0.18, W*0.18, W*0.16, W*0.15]
    t4 = Table(det_data, colWidths=det_cw, repeatRows=1)
    t4_style = [
        ("BACKGROUND",   (0,0), (-1,0), BLUE),
        ("INNERGRID",    (0,0), (-1,-1), 0.2, MGRAY),
        ("BOX",          (0,0), (-1,-1), 0.5, MGRAY),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("FONTSIZE",     (0,1), (-1,-1), 8),
    ]
    for i in range(1, len(det_data)):
        if i % 2 == 0:
            t4_style.append(("BACKGROUND", (0,i), (-1,i), LGRAY))
    t4.setStyle(TableStyle(t4_style))
    story += [t4, Spacer(1, 0.5*cm)]

    # ── footer ─────────────────────────────────────────────────────────────
    generated = datetime.now().strftime("%d %b %Y %H:%M")
    story.append(HRFlowable(width="100%", thickness=0.5, color=MGRAY))
    story.append(Paragraph(
        f"Generated by GATE Result Calculator  •  {generated}", FOOTER))

    doc.build(story)
    print(f"  Report saved -> {pdf_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=== GATE Result Calculator ===\n")

    paper_type = prompt_choice("Paper type", ["CS", "DA"])
    shift      = prompt_text("Shift (e.g. 1, 2, morning, afternoon)")
    date       = prompt_text("Date (e.g. 2026-02-15)")

    shift_safe = re.sub(r"[^\w-]", "_", shift)
    date_safe  = re.sub(r"[^\w-]", "-", date)
    ak_name    = f"{paper_type}_shift{shift_safe}_{date_safe}_answer_key.csv"
    ak_path    = _HERE / "answer_keys" / ak_name

    if not ak_path.exists():
        print(f"\n  Answer key not found: {ak_path}")
        print("  Run answer_parser.py first for this paper/shift/date.")
        sys.exit(1)

    print(f"\n  Found answer key: {ak_path.name}")
    answer_key = load_answer_key(ak_path)
    # build serial_no → ak_row lookup
    ak_map = {row["serial_no"]: row for row in answer_key}

    # ── parse response ───────────────────────────────────────────────────────
    print()
    source = prompt_text("Response sheet (HTML file path or URL)")
    html   = load_html(source)

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    info      = parse_info(soup)
    responses = parse_responses(soup)

    cid   = info.get("Candidate ID",   "UNKNOWN")
    cname = info.get("Candidate Name", "").strip()
    print(f"\n  Candidate : {cid}  |  {cname}")
    print(f"  Responses : {len(responses)} questions parsed")

    # ── match & score ────────────────────────────────────────────────────────
    results  = []
    unmatched = 0
    for resp in responses:
        qid = resp["question_id"]
        if qid not in ak_map:
            unmatched += 1
            continue
        results.append(score_question(ak_map[qid], resp))

    if unmatched:
        print(f"  Warning   : {unmatched} question(s) not found in answer key "
              f"(wrong paper/shift/date?)")

    # ── summary ──────────────────────────────────────────────────────────────
    total      = round(sum(r["marks_awarded"] for r in results), 2)
    max_marks  = sum(float(row["marks_correct"]) for row in answer_key)
    correct    = sum(1 for r in results if r["status"] == "correct")
    incorrect  = sum(1 for r in results if r["status"] == "incorrect")
    not_att    = sum(1 for r in results if r["status"] == "not_attempted")

    type_stats = {qt: type_metrics(results, qt)
                  for qt in ["MCQ", "MSQ", "NAT"]
                  if any(r["q_type"] == qt for r in results)}

    print("\n" + "─" * 40)
    print(f"  Score          : {total} / {max_marks}")
    print(f"  Correct        : {correct}")
    print(f"  Incorrect      : {incorrect}")
    print(f"  Not attempted  : {not_att}")
    print("─" * 40)
    for qt, st in type_stats.items():
        print(f"  {qt:<6} : {st['correct']}C / {st['incorrect']}W / "
              f"{st['not_attempted']}NA  ({st['marks']} marks)")
    print("─" * 40)

    # ── save detailed report ─────────────────────────────────────────────────
    cname_safe = re.sub(r"[^\w]", "-", cname).strip("-")
    out_base   = f"{cid}_{cname_safe}" if cname_safe else cid

    dir_json = _HERE / "results" / "json";  dir_json.mkdir(parents=True, exist_ok=True)
    dir_csv  = _HERE / "results" / "csv";   dir_csv.mkdir(parents=True, exist_ok=True)
    dir_pdf  = _HERE / "results" / "pdf";   dir_pdf.mkdir(parents=True, exist_ok=True)

    json_path = dir_json / f"{out_base}_result.json"
    csv_path  = dir_csv  / f"{out_base}_result.csv"
    pdf_path  = dir_pdf  / f"{out_base}_result.pdf"

    report = {
        "candidate_id":   cid,
        "candidate_name": cname,
        "paper":          paper_type,
        "shift":          shift,
        "date":           date,
        "total_marks":    total,
        "max_marks":      max_marks,
        "correct":        correct,
        "incorrect":      incorrect,
        "not_attempted":  not_att,
        "by_type":        type_stats,
        "details":        results,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if results:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

    print(f"\n  Report saved -> {json_path}")
    print(f"  Report saved -> {csv_path}")

    # ── PDF ─────────────────────────────────────────────────────────────
    summary_data = {
        "total":        total,
        "max_marks":    max_marks,
        "correct":      correct,
        "incorrect":    incorrect,
        "not_attempted": not_att,
        "total_qs":     len(results),
    }
    candidate_data = {
        "id":    cid,
        "name":  cname,
        "paper": paper_type,
        "shift": shift,
        "date":  date,
    }
    generate_pdf_report(pdf_path, candidate_data, summary_data, type_stats, results)


if __name__ == "__main__":
    main()
