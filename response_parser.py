#!/usr/bin/env python3
"""
GATE Response Sheet Parser
==========================
Parses a GATE candidate response HTML (local file or URL) and outputs
JSON + CSV into the  responses/  folder.

Usage:
    python response_parser.py                        # prompts for input
    python response_parser.py <html_path_or_url>     # direct arg

Output filename is auto-built from the candidate info inside the page:
    responses/<CandidateID>_<CandidateName>.json / .csv

Each row contains:
    question_id  |  q_type  |  status  |  chosen_option
"""

import sys, re, json, csv, os
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
import gzip, zlib

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("beautifulsoup4 is required.  pip install beautifulsoup4")


# ---------------------------------------------------------------------------
# Canonical option mapping (GATE shuffles A/B/C/D per candidate)
# Image filename encodes canonical letter: e.g.  ga8q2c.png  → canonical C
# ---------------------------------------------------------------------------
_OPT_RE = re.compile(r"([a-dA-D])(v\d+)?\.png$", re.IGNORECASE)


def _canonical_from_filename(fname: str) -> str:
    """Return the canonical option letter encoded in an image filename."""
    m = _OPT_RE.search(fname)
    return m.group(1).upper() if m else ""


def _build_option_map(left_tbl) -> dict:
    """Return {displayed_label -> canonical_letter} for all A/B/C/D options."""
    opt_map: dict = {}
    if not left_tbl:
        return opt_map
    for td in left_tbl.find_all("td"):
        # Find label: any td whose text starts with A/B/C/D followed by . or )
        text = td.get_text(strip=True)
        if not text:
            continue
        first = text[0].upper()
        if first not in "ABCD":
            continue
        if len(text) < 2 or text[1] not in ".)":
            continue
        display_label = first
        img = td.find("img")
        if img:
            # prefer name attr; fall back to src (may be absolute URL or relative path)
            fname = img.get("name", "").strip() or img.get("src", "").strip()
            canonical = _canonical_from_filename(fname)
            if canonical:
                opt_map[display_label] = canonical
        else:
            # Text-only option (no image shuffle) – label IS the canonical letter
            opt_map[display_label] = display_label
    return opt_map


def _apply_option_map(chosen: str, opt_map: dict, q_type: str) -> str:
    """Translate displayed option label(s) to canonical using opt_map."""
    raw = chosen.strip()
    if not raw or raw in ("--", " -- ", "- -"):
        return ""
    if q_type == "MSQ":
        parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
        canonical_parts = [opt_map.get(p, p) for p in parts]
        return ";".join(sorted(canonical_parts))
    return opt_map.get(raw.upper(), raw.upper())


# ---------------------------------------------------------------------------
# Load HTML from file path or URL
# ---------------------------------------------------------------------------
def _decompress(raw: bytes) -> bytes:
    """Decompress gzip/deflate if needed, detected by header or magic bytes."""
    enc_hint = ""  # caller can pass Content-Encoding here if known
    # gzip magic bytes: 1f 8b
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    # deflate: zlib magic bytes 78 9c / 78 01 / 78 da / 78 5e
    if raw[:1] == b"\x78":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                pass
    return raw


def load_html(source: str) -> str:
    if source.startswith("http://") or source.startswith("https://"):
        print(f"Fetching {source} ...")
        try:
            req = Request(source, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Encoding": "gzip, deflate",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Connection": "keep-alive",
            })
            with urlopen(req, timeout=30) as r:
                raw = r.read()
            raw = _decompress(raw)
            return raw.decode("utf-8", errors="replace")
        except URLError as e:
            sys.exit(f"Failed to fetch URL: {e}")
    else:
        p = Path(source)
        if not p.exists():
            sys.exit(f"File not found: {source}")
        return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Parse personal info from the first <table border="1"> in .main-info-pnl
# ---------------------------------------------------------------------------
def parse_info(soup: BeautifulSoup) -> dict:
    info = {}
    pnl = soup.find("div", class_="main-info-pnl")
    if not pnl:
        return info
    tbl = pnl.find("table")
    if not tbl:
        return info
    for tr in tbl.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 2:
            key = tds[0].get_text(strip=True)
            val = tds[1].get_text(strip=True)
            info[key] = val
    return info


# ---------------------------------------------------------------------------
# Build output filename from candidate info
# ---------------------------------------------------------------------------
def make_filename(info: dict) -> str:
    cid  = info.get("Candidate ID", "UNKNOWN").strip()
    name = info.get("Candidate Name", "").strip()
    name_safe = re.sub(r"[^\w]", "-", name).strip("-")
    return f"{cid}_{name_safe}" if name_safe else cid


# ---------------------------------------------------------------------------
# Parse all question panels
# Each .question-pnl has:
#   - left  table.questionRowTbl  → NAT "Given Answer"
#   - right table.menu-tbl        → QID, qtype, status, chosen option
# ---------------------------------------------------------------------------
def parse_responses(soup: BeautifulSoup) -> list:
    records = []

    for pnl in soup.find_all("div", class_="question-pnl"):
        # ── right side: menu-tbl ───────────────────────────────────────────
        menu = pnl.find("table", class_="menu-tbl")
        if not menu:
            continue

        # Parse menu-tbl: handles both well-formed (<tr> wrapped) and raw CDN
        # HTML where <td> pairs appear outside any <tr>.
        meta = {}
        all_tds = menu.find_all("td")
        i = 0
        while i < len(all_tds) - 1:
            key_raw = all_tds[i].get_text(strip=True)
            val_raw = all_tds[i + 1].get_text(strip=True)
            # label cells end with ":" and value cells don't
            if key_raw.endswith(":"):
                key = key_raw.rstrip(":").strip()
                meta[key] = val_raw
                i += 2
            else:
                i += 1

        q_type   = meta.get("Question Type", "").upper()
        q_id     = meta.get("Question ID",   "").strip()
        status   = meta.get("Status",        "").strip()
        chosen   = meta.get("Chosen Option", "").strip()

        # ── left side: questionRowTbl → NAT "Given Answer" + option images ──
        left_tbl = pnl.find("table", class_="questionRowTbl")
        nat_answer = ""
        if left_tbl:
            for tr in left_tbl.find_all("tr"):
                tds = tr.find_all("td")
                for i, td in enumerate(tds):
                    if "Given Answer" in td.get_text():
                        if i + 1 < len(tds):
                            nat_answer = tds[i + 1].get_text(strip=True)

        # ── normalise answer (translate displayed labels → canonical) ───────
        if q_type == "NAT":
            answer = nat_answer
        elif q_type in ("MCQ", "MSQ"):
            opt_map = _build_option_map(left_tbl)
            answer = _apply_option_map(chosen, opt_map, q_type)
        else:
            answer = chosen.strip()

        if not q_id:
            continue

        records.append({
            "question_id":    q_id,
            "q_type":         q_type,
            "status":         status,
            "chosen_option":  answer,
        })

    return records


# ---------------------------------------------------------------------------
# Save outputs
# ---------------------------------------------------------------------------
def save(records: list, base_name: str, out_dir: Path):
    dir_json = out_dir / "json";  dir_json.mkdir(parents=True, exist_ok=True)
    dir_csv  = out_dir / "csv";   dir_csv.mkdir(parents=True, exist_ok=True)

    json_path = dir_json / f"{base_name}.json"
    csv_path  = dir_csv  / f"{base_name}.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    print(f"Saved -> {json_path}")

    if records:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)
        print(f"Saved -> {csv_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) == 2:
        source = sys.argv[1]
    elif len(sys.argv) == 1:
        source = input("HTML file path or URL: ").strip()
        if not source:
            sys.exit("No input provided.")
    else:
        sys.exit("Usage: python response_parser.py [<html_path_or_url>]")

    html = load_html(source)
    soup = BeautifulSoup(html, "html.parser")

    # personal info
    info = parse_info(soup)
    print("\n--- Candidate Info ---")
    for k, v in info.items():
        print(f"  {k}: {v}")

    base_name = make_filename(info)
    print(f"\nOutput base name: {base_name}")

    # responses
    records = parse_responses(soup)
    print(f"Parsed {len(records)} questions")

    # save into responses/ folder next to this script
    out_dir = Path(__file__).parent / "responses"
    save(records, base_name, out_dir)


if __name__ == "__main__":
    main()
