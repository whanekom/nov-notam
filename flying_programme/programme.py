"""Read the NHV flying programme table out of the Ops email.

Ops send the programme two ways, depending on who is on shift:
  * as a real table pasted into the email  -> read_programme_html()
  * as a picture of the spreadsheet        -> read_programme() (OCR)
Both return the same structure: the programme lines as plain dictionaries.
"""

from __future__ import annotations

import difflib
import re

from html.parser import HTMLParser

try:  # only needed when the programme arrives as a picture
    import cv2
    import numpy as np
    import pytesseract
except ImportError:  # pragma: no cover
    cv2 = np = pytesseract = None

COLUMNS = ["crew", "report", "std", "route", "aircraft", "callsign", "remarks"]
HEADER_NAMES = {
    "crew": "CREW",
    "report": "CREW REPORT",
    "std": "STD (L)",
    "route": "ROUTE",
    "aircraft": "AIRCRAFT",
    "callsign": "CALLSIGN",
    "remarks": "REMARKS",
}
TARGET_WIDTH = 3900  # upscale so the small spreadsheet text is big enough to OCR


class ProgrammeReadError(Exception):
    """The image could not be read as a flying programme table."""


# --------------------------------------------------------------------------
# Grid detection
# --------------------------------------------------------------------------

def _line_positions(mask: np.ndarray, axis: int, min_fraction: float) -> list[int]:
    """Centres of the grid lines found in a line mask along one axis."""
    counts = (mask > 0).sum(axis=axis)
    length = mask.shape[axis]
    hits = np.where(counts >= length * min_fraction)[0]
    if hits.size == 0:
        return []
    groups, start, prev = [], hits[0], hits[0]
    for h in hits[1:]:
        if h - prev > 3:
            groups.append((start + prev) // 2)
            start = h
        prev = h
    groups.append((start + prev) // 2)
    return [int(g) for g in groups]


def _find_grid(gray: np.ndarray) -> tuple[list[int], list[int]]:
    """Return (x positions of vertical lines, y positions of horizontal lines)."""
    h, w = gray.shape
    dark = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)[1]

    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 3, 10), 1))
    horiz = cv2.morphologyEx(dark, cv2.MORPH_OPEN, horiz_kernel)
    ys = _line_positions(horiz, axis=1, min_fraction=0.6)

    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 4, 10)))
    vert = cv2.morphologyEx(dark, cv2.MORPH_OPEN, vert_kernel)
    xs = _line_positions(vert, axis=0, min_fraction=0.4)
    return xs, ys


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------

# Only characters a programme can contain. The quotes keep the space in the list.
_TESS = (
    '--oem 1 -c "tessedit_char_whitelist='
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/:,.-()&+ "'
)


def _ocr_cell(cell: np.ndarray) -> tuple[str, str]:
    """Read one cell twice, with two different image treatments.

    Returns (reading, second_reading). They normally agree; when they do not,
    the caller records the cell as doubtful rather than trusting either one.
    """
    if cell.size == 0:
        return "", ""
    ink = np.where(cell < 150)
    if ink[0].size < 12:  # nothing but background
        return "", ""
    y0, y1, x0, x1 = ink[0].min(), ink[0].max(), ink[1].min(), ink[1].max()
    background = int(np.median(cell))  # highlighted cells are not white
    tight = cv2.copyMakeBorder(
        cell[y0:y1 + 1, x0:x1 + 1], 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=background
    )
    first = _clean(pytesseract.image_to_string(tight, config="--psm 7 " + _TESS))
    binary = cv2.threshold(tight, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    second = _clean(pytesseract.image_to_string(binary, config="--psm 6 " + _TESS))
    if not first and not second:  # there is ink here, so try once more as a single word
        first = second = _clean(pytesseract.image_to_string(binary, config="--psm 8 " + _TESS))
    return first, second


def _clean(text: str) -> str:
    text = text.replace("|", "I").replace("\n", " ")
    text = re.sub(r"[_~`'\"“”‘’]", "", text)
    return re.sub(r"\s+", " ", text).strip(" .").upper()


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------
# Table parsing
# --------------------------------------------------------------------------

def read_programme(image_bytes: bytes) -> dict:
    """Parse a programme image.

    Returns {"date": str|None, "rows": [...], "ops": [...], "doubts": [...]}.
    Each row is a dict with the keys in COLUMNS plus its own "doubts" (cells
    the two OCR passes read differently). The top-level "doubts" lists only
    doubtful crew cells, which matter even on lines that are not yours.
    Raises ProgrammeReadError if the picture is not the programme table.
    """
    if cv2 is None:
        raise ProgrammeReadError("the OCR libraries are not installed")
    data = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ProgrammeReadError("not a readable image")
    scale = max(1.0, TARGET_WIDTH / img.shape[1])
    if scale > 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    xs, ys = _find_grid(gray)
    if len(xs) < 4 or len(ys) < 4:
        raise ProgrammeReadError("no table grid found in the image")

    pad = max(4, int(2 * scale))
    grid: list[list[str]] = []
    alt_grid: list[list[str]] = []
    row_tops: list[int] = []
    for top, bottom in zip(ys, ys[1:]):
        if bottom - top < 6 * scale:
            continue
        row, alt_row = [], []
        for left, right in zip(xs, xs[1:]):
            if right - left < 10 * scale:
                continue
            cell = gray[top + pad:bottom - pad, left + pad:right - pad]
            first, second = _ocr_cell(cell)
            row.append(first or second)
            alt_row.append(second or first)
        grid.append(row)
        alt_grid.append(alt_row)
        row_tops.append(top)

    rows, ops, doubts, header_idx = _rows_from_grid(grid, alt_grid)

    date = None
    for row in grid[:header_idx]:
        for text in row:
            date = date or _parse_date(text)
    return {
        "source": "picture",
        "date": date or _find_date(img, row_tops[header_idx]),
        "rows": rows,
        "ops": ops,
        "doubts": doubts,
    }


def _rows_from_grid(grid: list[list[str]], alt_grid: list[list[str]] | None = None):
    """Turn a grid of cell texts into programme lines.

    alt_grid is the second OCR reading of the same cells (pictures only).
    Returns (rows, ops, doubts, index of the header row).
    """
    from_ocr = alt_grid is not None
    alt_grid = alt_grid or grid
    header_idx = None
    for i, row in enumerate(grid):
        if sum(1 for c in row if c) >= 4 and any(_similar(c, "CREW") > 0.8 for c in row):
            header_idx = i
            break
    if header_idx is None:
        raise ProgrammeReadError("could not find the CREW / STD / ROUTE header row")

    # Work out which column is which from the header labels, not their position.
    col_for: dict[str, int] = {}
    header = grid[header_idx]
    for key, name in HEADER_NAMES.items():
        best, best_score = None, 0.6
        for ci, text in enumerate(header):
            if ci in col_for.values() or not text:
                continue
            score = _similar(text, name)
            if score > best_score:
                best, best_score = ci, score
        if best is not None:
            col_for[key] = best
    if "crew" not in col_for:
        raise ProgrammeReadError("could not find the CREW column")

    def cell(row: list[str], key: str) -> str:
        idx = col_for.get(key)
        return row[idx] if idx is not None and idx < len(row) else ""

    rows, ops, in_ops = [], [], False
    doubts: list[str] = []
    prev_crew, prev_blank = "", True
    for raw, alt in zip(grid[header_idx + 1:], alt_grid[header_idx + 1:]):
        rec = {k: _pick(k, cell(raw, k), cell(alt, k)) for k in COLUMNS}
        rec["crew_alt"] = cell(alt, "crew")
        rec["crew_raw"] = cell(raw, "crew")
        if from_ocr:
            rec["crew"] = _repair_crew(rec["crew"])
        if not any(rec[k] for k in COLUMNS):
            prev_blank = True
            continue
        if _similar(rec["crew"], "OPS") > 0.8 and not rec["std"]:
            in_ops = True
            continue
        if in_ops:
            ops.append({"shift": rec["crew"], "name": rec["report"]})
            continue
        def squeeze(text: str) -> str:  # a stray space is not worth reporting
            return text.replace(" ", "")

        rec["doubts"] = [
            f"{cell(raw, k)} or {cell(alt, k)}"
            for k in COLUMNS
            if squeeze(cell(raw, k)) != squeeze(cell(alt, k))
        ]
        if squeeze(rec["crew"]) != squeeze(rec["crew_raw"]):
            rec["doubts"].append(f"crew read as {rec['crew_raw']}")
        if squeeze(rec["crew_raw"]) != squeeze(rec["crew_alt"]):
            doubts.append(f"{rec['crew_raw']} or {rec['crew_alt']}")
        # A second line for the same crew is sometimes left with the crew cell blank.
        rec["crew_inherited"] = False
        if not rec["crew"] and prev_crew and not prev_blank:
            rec["crew"], rec["crew_inherited"] = prev_crew, True
        prev_crew, prev_blank = rec["crew"], False
        rows.append(rec)
    return rows, ops, doubts, header_idx


# --------------------------------------------------------------------------
# Programme pasted as a real table (HTML email body)
# --------------------------------------------------------------------------

class _TableGrabber(HTMLParser):
    """Collects every <table> in an HTML document as rows of cell text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack and self._stack[-1]:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._stack:
            text = "".join(self._cell).replace("\xa0", " ")
            self._stack[-1][-1].append(re.sub(r"\s+", " ", text).strip().upper())
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def read_programme_html(html: str) -> dict:
    """Parse a programme that was pasted into the email as a real table.

    Raises ProgrammeReadError if the email has no programme table.
    """
    grabber = _TableGrabber()
    grabber.feed(html)
    for grid in grabber.tables:
        try:
            rows, ops, _, _ = _rows_from_grid(grid)
        except ProgrammeReadError:
            continue
        if rows:
            return {"source": "table", "date": None, "rows": rows, "ops": ops, "doubts": []}
    raise ProgrammeReadError("no programme table in the email text")


def _find_date(img: np.ndarray, header_top: int) -> str | None:
    """The date sits in a yellow cell above the table's top-left corner."""
    strip = img[: max(header_top - 2, 1)]
    if strip.shape[0] < 10:
        return None
    blue, green, red = cv2.split(strip)
    yellow = ((red > 180) & (green > 180) & (blue < 140)).astype(np.uint8)
    ys_, xs_ = np.where(yellow > 0)
    if ys_.size < 50:
        return None
    box = cv2.cvtColor(
        strip[ys_.min():ys_.max() + 1, xs_.min():xs_.max() + 1], cv2.COLOR_BGR2GRAY
    )
    margin = max(4, box.shape[0] // 8)  # stay clear of the cell border
    first, second = _ocr_cell(box[margin:-margin, margin:-margin])
    return _parse_date(first) or _parse_date(second)


def _parse_date(text: str) -> str | None:
    """Pull a dd/mm/yy or dd/mm/yyyy date out of text, as dd/mm/yyyy."""
    m = re.search(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4}|\d{2})", text)
    if not m:
        return None
    year = m.group(3) if len(m.group(3)) == 4 else "20" + m.group(3)
    return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{year}"


# --------------------------------------------------------------------------
# Finding one pilot's lines
# --------------------------------------------------------------------------

# What a well-read cell looks like; used to choose between the two OCR readings.
_EXPECTED = {
    "crew": re.compile(r"[A-Z]{5}(/[A-Z]{5})*"),
    "std": re.compile(r"\d{2}:\d{2}"),
    "aircraft": re.compile(r"G-[A-Z]{4}"),
    "callsign": re.compile(r"[A-Z]{3,5}\d{2,4}"),
}


def _pick(column: str, first: str, second: str) -> str:
    pattern = _EXPECTED.get(column)
    if pattern and not pattern.fullmatch(first) and pattern.fullmatch(second):
        return second
    return first


CODE_LEN = 5  # crew codes on the programme are five letters (ZOMUN, CHGUE ...)

_CONFUSIONS = str.maketrans({"0": "O", "1": "I", "L": "I", "5": "S", "8": "B", "2": "Z"})


def _repair_crew(text: str) -> str:
    """Put back a slash the OCR lost or misread between two five-letter codes.

    "ZOMUNMWIHAN" (slash read as a letter) and "ZOMUNWIHAN" (slash dropped)
    both become "ZOMUN/WIHAN". Anything else is returned unchanged.
    """
    text = re.sub(r"\s*/\s*", "/", text.strip())
    squeezed = text.replace(" ", "")
    if _EXPECTED["crew"].fullmatch(squeezed):  # a stray space inside a code
        return squeezed
    if "/" in text or " " in text or not text.isalpha():
        return text
    if len(text) == 2 * CODE_LEN + 1:
        return text[:CODE_LEN] + "/" + text[CODE_LEN + 1:]
    if len(text) == 2 * CODE_LEN:
        return text[:CODE_LEN] + "/" + text[CODE_LEN:]
    return text


def _canon(code: str) -> str:
    code = re.sub(r"[^A-Z0-9]", "", code.upper()).replace("VV", "W")
    return code.translate(_CONFUSIONS)


def _nearly_contains(text: str, target: str) -> bool:
    """True if text holds target with one letter wrong or one letter missing."""
    n = len(target)
    for i in range(len(text) - n + 1):
        if sum(a != b for a, b in zip(text[i:i + n], target)) <= 1:
            return True
    return any(target[:i] + target[i + 1:] in text for i in range(n)) if n > 3 else False


def crew_codes(rows: list[dict]) -> list[str]:
    """Every crew code on the programme, in order, without repeats."""
    seen: list[str] = []
    for r in rows:
        for token in re.split(r"[/\s,&+]+", r["crew"]):
            if token and token not in seen:
                seen.append(token)
    return seen


def find_duties(rows: list[dict], my_code: str) -> tuple[list[dict], list[dict]]:
    """Return (definite, possible) rows for my_code.

    definite: the code is on the line (allowing look-alike characters such as
    1/I or 0/O, and a misread slash next to it). possible: a code one letter
    away, which is either an OCR slip or a colleague with a similar code -
    flagged for checking, never assumed.
    """
    target = _canon(my_code)
    definite, possible = [], []
    for r in rows:
        readings = [r["crew"], r.get("crew_raw", ""), r.get("crew_alt", "")]
        crew_tokens = [_canon(t) for text in readings for t in re.split(r"[/\s,&+]+", text) if t]
        other_text = " ".join(r[k] for k in COLUMNS if k != "crew")
        other_tokens = [_canon(t) for t in re.findall(r"[A-Z0-9]+", other_text)]
        if any(target in t for t in crew_tokens) or target in other_tokens:
            definite.append(r)
        elif any(_nearly_contains(t, target) for t in crew_tokens):
            possible.append(r)
    return definite, possible
