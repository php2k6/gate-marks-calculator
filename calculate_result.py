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
        from reportlab.lib.units import cm, mm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, KeepTogether
        )
        from reportlab.pdfgen import canvas as rl_canvas
    except ImportError:
        print("  [PDF skipped] reportlab not installed. pip install reportlab")
        return

    # ── palette ─────────────────────────────────────────────────────────────
    BLUE     = colors.HexColor("#1a3a6b")
    BLUE_MID = colors.HexColor("#2d5aa0")
    ORANGE   = colors.HexColor("#e87722")
    LGRAY    = colors.HexColor("#f4f6fa")
    MGRAY    = colors.HexColor("#d0d7e6")
    DGRAY    = colors.HexColor("#6b7280")
    GREEN    = colors.HexColor("#1e7e34")
    GREEN_LT = colors.HexColor("#d4edda")
    RED      = colors.HexColor("#c0392b")
    RED_LT   = colors.HexColor("#fde8e8")
    AMBER    = colors.HexColor("#b45309")
    AMBER_LT = colors.HexColor("#fef9c3")
    WHITE    = colors.white
    BLACK    = colors.black

    PAGE_W, PAGE_H = A4
    L_MAR = R_MAR = 1.8 * cm
    T_MAR = 2.2 * cm   # leave room for per-page top bar
    B_MAR = 2.0 * cm   # leave room for per-page footer bar
    W = PAGE_W - L_MAR - R_MAR
    generated_str = datetime.now().strftime("%d %b %Y, %H:%M")

    # ── per-page decorator canvas ────────────────────────────────────────────
    class _PageCanvas(rl_canvas.Canvas):
        """Draws orange top bar + branded footer bar on every page."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for i, state in enumerate(self._saved, 1):
                self.__dict__.update(state)
                self._decorate(i, total)
                rl_canvas.Canvas.showPage(self)
            rl_canvas.Canvas.save(self)

        def _decorate(self, page_num, total_pages):
            # ── orange accent bar at top ────────────────────────────────
            self.setFillColor(ORANGE)
            self.rect(L_MAR, PAGE_H - T_MAR + 6*mm,
                      W, 3.5, fill=1, stroke=0)

            # ── "GATE Result Calculator" text in top-right ──────────────
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(BLUE)
            self.drawRightString(L_MAR + W,
                                 PAGE_H - T_MAR + 7.5*mm,
                                 "GATE Result Calculator")

            # ── footer bar ──────────────────────────────────────────────
            fy = B_MAR - 1.4 * cm
            self.setFillColor(BLUE)
            self.rect(L_MAR, fy, W, 18, fill=1, stroke=0)
            self.setFillColor(WHITE)
            self.setFont("Helvetica", 7)
            self.drawString(L_MAR + 5, fy + 5.5,
                            f"Generated: {generated_str}   \u2022   Results are indicative only.")
            self.setFont("Helvetica-Bold", 7)
            self.drawRightString(L_MAR + W - 5, fy + 5.5,
                                 f"github.com/php2k6   \u2022   Page {page_num} of {total_pages}")

    # ── document ─────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=L_MAR, rightMargin=R_MAR,
        topMargin=T_MAR, bottomMargin=B_MAR,
    )
    story = []

    # ── text styles ──────────────────────────────────────────────────────────
    def _sty(name, **kw):
        return ParagraphStyle(name, **kw)

    TITLE   = _sty("title",  fontSize=22, fontName="Helvetica-Bold",
                   textColor=WHITE, alignment=TA_LEFT, spaceAfter=2,
                   leading=26)
    SUBTITLE= _sty("subt",   fontSize=10, fontName="Helvetica",
                   textColor=colors.HexColor("#c8d8f0"), alignment=TA_LEFT)
    CREDIT  = _sty("credit", fontSize=8,  fontName="Helvetica",
                   textColor=ORANGE, alignment=TA_RIGHT)
    LABEL   = _sty("lbl",    fontSize=8,  fontName="Helvetica-Bold",
                   textColor=DGRAY)
    BODY    = _sty("body",   fontSize=9,  fontName="Helvetica",
                   textColor=BLACK)
    SECT    = _sty("sect",   fontSize=11, fontName="Helvetica-Bold",
                   textColor=BLUE, spaceBefore=14, spaceAfter=5,
                   borderPad=2)
    HDR     = _sty("thdr",   fontSize=9,  fontName="Helvetica-Bold",
                   textColor=WHITE, alignment=TA_CENTER)
    CELL    = _sty("tcell",  fontSize=9,  fontName="Helvetica",
                   alignment=TA_CENTER)
    CELL_L  = _sty("tcelll", fontSize=9,  fontName="Helvetica",
                   alignment=TA_LEFT)
    BOLD_C  = _sty("boldc",  fontSize=9,  fontName="Helvetica-Bold",
                   alignment=TA_CENTER)

    # ── helper: section heading with left accent bar ─────────────────────────
    def section_heading(text):
        data = [[Paragraph(text, SECT)]]
        t = Table(data, colWidths=[W])
        t.setStyle(TableStyle([
            ("LINEBEFOFE",  (0,0),(0,0), 4, ORANGE),   # accent stripe
            ("BACKGROUND",  (0,0),(-1,-1), LGRAY),
            ("LEFTPADDING", (0,0),(-1,-1), 6),
            ("TOPPADDING",  (0,0),(-1,-1), 4),
            ("BOTTOMPADDING",(0,0),(-1,-1), 4),
            ("LINEABOVE",   (0,0),(-1,0),  1.5, ORANGE),
        ]))
        return t

    # ── banner header ────────────────────────────────────────────────────────
    banner_left = [
        [Paragraph("GATE Result Report", TITLE)],
        [Paragraph(
            f"Paper: <b>{candidate['paper']}</b> &nbsp;|\u200a "
            f"Shift: <b>{candidate['shift']}</b> &nbsp;|\u200a "
            f"Date: <b>{candidate['date']}</b>", SUBTITLE)],
    ]
    banner_right = [
        [Paragraph("github.com/php2k6", CREDIT)],
        [Paragraph("GATE Result Calculator", CREDIT)],
    ]
    ban_l = Table(banner_left,  colWidths=[W * 0.65])
    ban_r = Table(banner_right, colWidths=[W * 0.35])
    ban_l.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), BLUE),
        ("LEFTPADDING",   (0,0),(-1,-1), 10),
        ("TOPPADDING",    (0,0),(-1,-1), 8),
        ("BOTTOMPADDING", (0,0),(-1,-1), 8),
    ]))
    ban_r.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), BLUE),
        ("RIGHTPADDING",  (0,0),(-1,-1), 10),
        ("TOPPADDING",    (0,0),(-1,-1), 12),
        ("BOTTOMPADDING", (0,0),(-1,-1), 8),
    ]))
    banner = Table([[ban_l, ban_r]], colWidths=[W * 0.65, W * 0.35])
    banner.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),(-1,-1), BLUE),
        ("LEFTPADDING",  (0,0),(-1,-1), 0),
        ("RIGHTPADDING", (0,0),(-1,-1), 0),
        ("TOPPADDING",   (0,0),(-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 0),
        ("LINEBELOW",    (0,-1),(-1,-1), 4, ORANGE),
    ]))
    story += [banner, Spacer(1, 0.45 * cm)]

    # ── candidate info ───────────────────────────────────────────────────────
    story.append(section_heading("Candidate Information"))
    story.append(Spacer(1, 0.15 * cm))
    ci = [[
        Paragraph("Candidate ID",   LABEL),
        Paragraph(str(candidate['id']),   BODY),
        Paragraph("Name",           LABEL),
        Paragraph(str(candidate['name']), BODY),
        Paragraph("Paper / Shift",  LABEL),
        Paragraph(f"{candidate['paper']} · Shift {candidate['shift']}", BODY),
    ]]
    ci_tbl = Table(ci, colWidths=[W*0.13, W*0.24, W*0.09, W*0.28, W*0.12, W*0.14])
    ci_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), LGRAY),
        ("BOX",           (0,0),(-1,-1), 0.8, MGRAY),
        ("INNERGRID",     (0,0),(-1,-1), 0.3, MGRAY),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
    ]))
    story += [ci_tbl, Spacer(1, 0.5 * cm)]

    # ── marks summary cards ──────────────────────────────────────────────────
    story.append(section_heading("Marks Summary"))
    story.append(Spacer(1, 0.15 * cm))

    pct = round(summary['total'] / summary['max_marks'] * 100, 1) \
          if summary['max_marks'] else 0

    def _card(top_val, top_lbl, bot_val, bot_lbl, accent, bg):
        TOP_V = _sty(f"cv_{top_lbl}", fontSize=20, fontName="Helvetica-Bold",
                     alignment=TA_CENTER, textColor=accent, leading=24)
        TOP_L = _sty(f"cl_{top_lbl}", fontSize=7.5, fontName="Helvetica",
                     alignment=TA_CENTER, textColor=DGRAY)
        BOT_V = _sty(f"bv_{bot_lbl}", fontSize=13, fontName="Helvetica-Bold",
                     alignment=TA_CENTER, textColor=accent, leading=16)
        BOT_L = _sty(f"bl_{bot_lbl}", fontSize=7, fontName="Helvetica",
                     alignment=TA_CENTER, textColor=DGRAY)
        d = [
            [Paragraph(str(top_val), TOP_V)],
            [Paragraph(top_lbl, TOP_L)],
            [HRFlowable(width="80%", thickness=0.5, color=MGRAY, hAlign="CENTER")],
            [Paragraph(str(bot_val), BOT_V)],
            [Paragraph(bot_lbl, BOT_L)],
        ]
        t = Table(d, colWidths=[W * 0.195])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), bg),
            ("LINEABOVE",     (0,0),(-1, 0), 3, accent),
            ("BOX",           (0,0),(-1,-1), 0.5, MGRAY),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ]))
        return t

    not_att = summary.get("not_attempted", 0)
    cards_row = [[
        _card(f"{summary['total']}",   "Total Marks",
              f"/ {summary['max_marks']}", "Max Marks",   BLUE,   LGRAY),
        _card(f"{pct}%",               "Percentage",
              f"{summary['total_qs']}", "Questions",      BLUE_MID, LGRAY),
        _card(summary['correct'],      "Correct",
              f"+{round(sum(r['marks_awarded'] for r in results if r['status']=='correct'),1)}",
              "Marks Earned",                             GREEN,  GREEN_LT),
        _card(summary['incorrect'],    "Incorrect",
              f"{round(sum(r['marks_awarded'] for r in results if r['status']=='incorrect'),1)}",
              "Marks Lost",                               RED,    RED_LT),
        _card(not_att,                 "Not Attempted",
              "0", "Marks Lost",                          AMBER,  AMBER_LT),
    ]]
    cards = Table(cards_row, colWidths=[W * 0.2] * 5)
    cards.setStyle(TableStyle([
        ("LEFTPADDING",  (0,0),(-1,-1), 3),
        ("RIGHTPADDING", (0,0),(-1,-1), 3),
        ("TOPPADDING",   (0,0),(-1,-1), 0),
        ("BOTTOMPADDING",(0,0),(-1,-1), 0),
    ]))
    story += [cards, Spacer(1, 0.55 * cm)]

    # ── per-type breakdown ───────────────────────────────────────────────────
    story.append(section_heading("Question Type Breakdown"))
    story.append(Spacer(1, 0.15 * cm))

    type_hdr_row = [[Paragraph(h, HDR) for h in
                     ["Type", "Total Qs", "Correct", "Incorrect",
                      "Not Attempted", "Marks"]]]
    type_rows = []
    for qt, st in type_stats.items():
        type_rows.append([
            Paragraph(qt, BOLD_C),
            Paragraph(str(st["total"]),         CELL),
            Paragraph(str(st["correct"]),        CELL),
            Paragraph(str(st["incorrect"]),      CELL),
            Paragraph(str(st["not_attempted"]),  CELL),
            Paragraph(str(st["marks"]),          CELL),
        ])
    BOLD_TOT = _sty("btot", fontSize=9, fontName="Helvetica-Bold",
                    alignment=TA_CENTER, textColor=BLUE)
    total_row = [Paragraph(v, BOLD_TOT) for v in [
        "TOTAL",
        str(summary["total_qs"]),
        str(summary["correct"]),
        str(summary["incorrect"]),
        str(not_att),
        str(summary["total"]),
    ]]
    type_data = type_hdr_row + type_rows + [total_row]
    cw6 = [W*0.12, W*0.14, W*0.14, W*0.15, W*0.22, W*0.23]
    t2  = Table(type_data, colWidths=cw6)
    t2s = [
        ("BACKGROUND",    (0,0), (-1, 0),  BLUE),
        ("BACKGROUND",    (0,-1),(-1,-1),  MGRAY),
        ("LINEABOVE",     (0,-1),(-1,-1),  1, BLUE),
        ("INNERGRID",     (0,0), (-1,-1),  0.3, MGRAY),
        ("BOX",           (0,0), (-1,-1),  0.6, MGRAY),
        ("TOPPADDING",    (0,0), (-1,-1),  6),
        ("BOTTOMPADDING", (0,0), (-1,-1),  6),
    ]
    for i in range(1, len(type_data) - 1):
        if i % 2 == 0:
            t2s.append(("BACKGROUND", (0,i), (-1,i), LGRAY))
    t2.setStyle(TableStyle(t2s))
    story += [t2, Spacer(1, 0.5 * cm)]

    # ── section breakdown ────────────────────────────────────────────────────
    sections = sorted({r["section"] for r in results if r.get("section")})
    if sections:
        story.append(section_heading("Section Breakdown"))
        story.append(Spacer(1, 0.15 * cm))
        sec_hdr_row = [[Paragraph(h, HDR) for h in
                        ["Section", "Total Qs", "Correct", "Incorrect",
                         "Not Attempted", "Marks"]]]
        sec_rows = []
        for sec in sections:
            sr   = [r for r in results if r.get("section") == sec]
            sc_  = sum(1 for r in sr if r["status"] == "correct")
            si_  = sum(1 for r in sr if r["status"] == "incorrect")
            sna_ = sum(1 for r in sr if r["status"] == "not_attempted")
            sm_  = round(sum(r["marks_awarded"] for r in sr), 2)
            sec_rows.append([
                Paragraph(sec,        BOLD_C),
                Paragraph(str(len(sr)), CELL),
                Paragraph(str(sc_),   CELL),
                Paragraph(str(si_),   CELL),
                Paragraph(str(sna_),  CELL),
                Paragraph(str(sm_),   CELL),
            ])
        sec_data = sec_hdr_row + sec_rows
        t3 = Table(sec_data, colWidths=cw6)
        t3s = [
            ("BACKGROUND",    (0,0),(-1, 0), BLUE),
            ("INNERGRID",     (0,0),(-1,-1), 0.3, MGRAY),
            ("BOX",           (0,0),(-1,-1), 0.6, MGRAY),
            ("TOPPADDING",    (0,0),(-1,-1), 6),
            ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ]
        for i in range(1, len(sec_data)):
            if i % 2 == 0:
                t3s.append(("BACKGROUND", (0,i), (-1,i), LGRAY))
        t3.setStyle(TableStyle(t3s))
        story += [t3, Spacer(1, 0.5 * cm)]

    # ── detailed question table ──────────────────────────────────────────────
    story.append(section_heading("Detailed Question Results"))
    story.append(Spacer(1, 0.15 * cm))

    det_hdr_row = [[Paragraph(h, HDR) for h in
                    ["Q No", "Type", "Section",
                     "Correct Ans", "Given Ans", "Status", "Marks"]]]
    STATUS_BG = {
        "correct":      (GREEN,  GREEN_LT),
        "incorrect":    (RED,    RED_LT),
        "not_attempted":(AMBER,  AMBER_LT),
    }
    MARK_STYLE = {
        "correct":       _sty("mk_c", fontSize=8, fontName="Helvetica-Bold",
                               alignment=TA_CENTER, textColor=GREEN),
        "incorrect":     _sty("mk_i", fontSize=8, fontName="Helvetica-Bold",
                               alignment=TA_CENTER, textColor=RED),
        "not_attempted": _sty("mk_n", fontSize=8, fontName="Helvetica-Bold",
                               alignment=TA_CENTER, textColor=AMBER),
    }
    STATUS_LABEL = {"correct": "Correct", "incorrect": "Incorrect",
                    "not_attempted": "Not Att."}

    det_rows = []
    row_bg_overrides = []   # (row_index, bg_color)
    for idx, r in enumerate(results, start=1):
        st    = r["status"]
        _, bg = STATUS_BG.get(st, (BLACK, WHITE))
        mk_st = MARK_STYLE.get(st, CELL)
        ST_ST = _sty(f"sts_{idx}", fontSize=8, fontName="Helvetica-Bold",
                     alignment=TA_CENTER,
                     textColor=STATUS_BG.get(st, (BLACK,WHITE))[0])
        det_rows.append([
            Paragraph(str(r["q_no"]),              CELL),
            Paragraph(r["q_type"],                 CELL),
            Paragraph(r.get("section", ""),        CELL),
            Paragraph(r["correct_answer"],         CELL),
            Paragraph(r["given_answer"] or "—",    CELL),
            Paragraph(STATUS_LABEL.get(st, st),    ST_ST),
            Paragraph(str(r["marks_awarded"]),     mk_st),
        ])
        row_bg_overrides.append((idx, bg))

    det_data = det_hdr_row + det_rows
    det_cw   = [W*0.09, W*0.08, W*0.13, W*0.18, W*0.18, W*0.17, W*0.17]
    t4 = Table(det_data, colWidths=det_cw, repeatRows=1)
    t4s = [
        ("BACKGROUND",    (0,0), (-1, 0), BLUE),
        ("INNERGRID",     (0,0), (-1,-1), 0.2, MGRAY),
        ("BOX",           (0,0), (-1,-1), 0.6, MGRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("FONTSIZE",      (0,1), (-1,-1), 8),
    ]
    for row_idx, bg in row_bg_overrides:
        if row_idx % 2 == 0:
            t4s.append(("BACKGROUND", (0, row_idx), (-1, row_idx), LGRAY))
    t4.setStyle(TableStyle(t4s))
    story += [t4, Spacer(1, 0.4 * cm)]

    doc.build(story, canvasmaker=_PageCanvas)
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
