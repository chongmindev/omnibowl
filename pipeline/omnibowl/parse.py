"""Stage 1: parse a textbook PDF.

This module currently implements the measurement layer: outline -> sections,
and every text line, light-filled panel, and internal link with its layout
features. Classifying lines into blocks (body, box, caption, definition,
exercise) and splitting sentences are separate steps that read these tables.
"""

import bisect
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pymupdf

from . import db

BOLD = 16
ITALIC = 2
# Leading number of an outline title: "3 Title", "3.1 Title", "Module 12 Title",
# "Unit IV Title", "1-3 Title". Roman numerals count only after a label word.
NUMBER_RE = re.compile(
    r"^(?:(?P<label>[A-Z][a-z]+)\s+)?"
    r"(?P<num>\d+(?:[.\-]\d+)*(?![\w])|(?<=\s)[IVXLC]+(?![\w]))[\s:.\-]*(?P<rest>.*)$")


def repair_mojibake(s: str) -> str:
    """Undo UTF-8 bytes that were decoded as cp1252 (e.g. 'â€™' -> '’')."""
    try:
        return s.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upsert_book(conn, path: Path, doc) -> int:
    slug = path.stem
    title = (doc.metadata or {}).get("title") or slug
    conn.execute(
        """INSERT INTO books (slug, title, path, sha256, page_count, page_width, page_height)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(slug) DO UPDATE SET title=excluded.title, path=excluded.path,
           sha256=excluded.sha256, page_count=excluded.page_count,
           page_width=excluded.page_width, page_height=excluded.page_height""",
        (slug, title, str(path), sha256(path), doc.page_count,
         doc[0].rect.width, doc[0].rect.height),
    )
    return conn.execute("SELECT id FROM books WHERE slug = ?", (slug,)).fetchone()[0]


LETTERED_RE = re.compile(r"^([A-Z][a-z]+)\s+[A-Z](?![\w])")


def split_number(title: str) -> tuple[str | None, str]:
    """('3.1', 'Rest of title') or (None, title). Keeps a label word: ('Unit IV', ...)."""
    m = NUMBER_RE.match(title)
    if not m:
        return None, title
    if m.group("label"):
        return f"{m.group('label')} {m.group('num')}", title
    return m.group("num"), m.group("rest") or title


def read_outline(doc) -> list[dict]:
    """Outline entries with kind, number, title, page_start, y_start, parent index.

    Roles come from structure, not titles: a top-level entry is content ("chapter")
    if it or any descendant carries a number. Unnumbered top-level entries before the
    first content entry are front matter; after the last one, back matter. Descendants
    inherit front/back matter; descendants of chapters are sections.
    """
    out: list[dict] = []
    stack: list[int] = []  # index of the open entry at each level
    for level, raw_title, page, dest in doc.get_toc(simple=False):
        title = repair_mojibake(raw_title).strip()
        to = dest.get("to")
        number, rest = split_number(title)
        del stack[level - 1:]
        out.append({"level": level, "title": rest, "number": number, "page_start": page,
                    "y_start": max(0.0, float(to.y)) if to is not None else 0.0,
                    "parent": stack[-1] if stack else None})
        stack.append(len(out) - 1)

    # A label also used with plain letters ("Appendix A", "Appendix B") is lettering,
    # so its "C" or "D" is not a Roman numeral.
    lettered = {m.group(1) for e in out if (m := LETTERED_RE.match(e["title"]))
                and not e["number"]}
    for e in out:
        if e["number"] and e["number"].split()[0] in lettered:
            e["number"] = None

    def top(i: int) -> int:
        while out[i]["parent"] is not None:
            i = out[i]["parent"]
        return i

    numbered_tops = {top(i) for i, e in enumerate(out) if e["number"]}
    tops = [i for i, e in enumerate(out) if e["parent"] is None]
    content = [i for i in tops if i in numbered_tops]
    first, last = (content[0], content[-1]) if content else (len(out), -1)
    for i, e in enumerate(out):
        t = top(i)
        if t < first:
            e["kind"] = "front_matter"
        elif t > last:
            e["kind"] = "back_matter"
        else:
            e["kind"] = "chapter" if i == t else "section"
    return out


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def locate_heading(pages: list[list[dict]], entry: dict, cfg: dict) -> float:
    """y of the first line at or below the outline destination whose text starts the
    entry's title (number included or not), or is exactly its number; the destination y
    if none matches. The title may wrap, so a line matches if it is a prefix of the
    title or vice versa."""
    want = {_key(entry["title"]), _key(f"{entry['number']} {entry['title']}")} - {""}
    page = pages[entry["page_start"] - 1] if 0 < entry["page_start"] <= len(pages) else []
    min_chars = cfg["heading_match_min_chars"]
    for ln in page:
        if ln["bbox"].y0 < entry["y_start"] - cfg["outline_y_tolerance"]:
            continue
        k = _key(ln["text"])
        if entry["number"] and k == _key(entry["number"]):
            return ln["bbox"].y0  # number set on its own line above the title
        if len(k) >= min_chars and any(w.startswith(k) or k.startswith(w) for w in want):
            return ln["bbox"].y0
    return entry["y_start"]


def is_panel_fill(fill, cfg: dict) -> bool:
    if fill is None:
        return False
    if all(c >= cfg["panel_white_channel"] for c in fill):
        return False
    return min(fill) >= cfg["panel_min_channel"]


def page_lines(page, cfg: dict) -> list[dict]:
    """Text lines on a page in reading order."""
    rows = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"]]
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            n = sum(len(s["text"]) for s in spans)
            sizes = Counter()
            for s in spans:
                sizes[round(s["size"], 1)] += len(s["text"])
            rows.append({
                "bbox": pymupdf.Rect(line["bbox"]),
                "size": sizes.most_common(1)[0][0],
                "bold_start": bool(spans[0]["flags"] & BOLD),
                "bold_frac": sum(len(s["text"]) for s in spans if s["flags"] & BOLD) / n,
                "italic_frac": sum(len(s["text"]) for s in spans if s["flags"] & ITALIC) / n,
                "text": text.strip(),
                "spans": [[s["text"], round(s["size"], 1), s["flags"]] for s in spans],
            })
    return order_lines(rows, page.rect.width, cfg)


def order_lines(rows: list[dict], width: float, cfg: dict) -> list[dict]:
    """Reading order. Single-column pages: top to bottom, then left to right.

    A page is two-column when enough lines sit wholly on each side of the page's
    vertical midline and few lines cross it. Then crossing lines above the columns come
    first, then the left column, then the right, then crossing lines below.
    """
    rows.sort(key=lambda r: (round(r["bbox"].y0), r["bbox"].x0))
    mid = width / 2
    left = [r for r in rows if r["bbox"].x1 <= mid]
    right = [r for r in rows if r["bbox"].x0 >= mid]
    cross = [r for r in rows if r["bbox"].x0 < mid < r["bbox"].x1]
    if (min(len(left), len(right)) < cfg["columns_min_lines"]
            or len(cross) > cfg["columns_max_cross_frac"] * len(rows)):
        return rows
    top_y = min(r["bbox"].y0 for r in left + right)
    above = [r for r in cross if r["bbox"].y0 < top_y]
    below = [r for r in cross if r["bbox"].y0 >= top_y]
    return above + left + right + below


def page_chars(page) -> list[tuple[float, float, str]]:
    """Every character on the page as (center x, center y, char), in extraction order."""
    out = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    x0, y0, x1, y1 = ch["bbox"]
                    out.append(((x0 + x1) / 2, (y0 + y1) / 2, ch["c"]))
            out.append((-1.0, -1.0, " "))  # line break
    return out


def chars_in(chars: list[tuple[float, float, str]], rect) -> str:
    """Text of the characters whose centers fall inside rect."""
    text = "".join(c if rect.contains(pymupdf.Point(x, y)) or x < 0 else "\0" for x, y, c in chars)
    return " ".join(part.strip() for part in text.split("\0") if part.strip())


def body_size(pages: list[list[dict]], first_page: int, last_page: int) -> float:
    """Modal non-bold font size (by characters) over a 1-based inclusive page range."""
    sizes = Counter()
    for lines in pages[first_page - 1:last_page]:
        for ln in lines:
            for text, size, flags in ln["spans"]:
                if not flags & BOLD:
                    sizes[size] += len(text)
    return sizes.most_common(1)[0][0] if sizes else 0.0


def run(book_path: Path, conn, cfg: dict) -> int:
    pcfg = cfg["parse"]
    doc = pymupdf.open(book_path)
    book_id = upsert_book(conn, book_path, doc)
    db.clear(conn, book_id, db.PARSE_TABLES)

    pages = [page_lines(doc[pno], pcfg) for pno in range(doc.page_count)]

    # Sections.
    outline = read_outline(doc)
    ids: list[int] = []
    for ord_, e in enumerate(outline):
        parent_id = ids[e["parent"]] if e["parent"] is not None else None
        cur = conn.execute(
            """INSERT INTO sections (book_id, parent_id, level, kind, number, title,
               page_start, y_start, ord) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (book_id, parent_id, e["level"], e["kind"], e["number"], e["title"],
             e["page_start"], e["y_start"], ord_),
        )
        ids.append(cur.lastrowid)
    # Outline destinations are often just the top of a page. Move each start down to
    # the line on that page (or the next) whose text begins the entry's title.
    for e in outline:
        e["y_start"] = locate_heading(pages, e, pcfg)
    # Start keys for assigning each line to the deepest section that has begun.
    starts = sorted(((e["page_start"], e["y_start"], ord_) for ord_, e in enumerate(outline)))
    start_keys = [(p, y) for p, y, _ in starts]
    tol = pcfg["outline_y_tolerance"]

    # Body size per level-1 range (chapters are scaled differently in this ebook).
    l1_pages = sorted({e["page_start"] for e in outline if e["level"] == 1})
    page_body: dict[int, float] = {}
    for start, end in zip([1] + l1_pages, l1_pages + [doc.page_count + 1]):
        if end <= start:
            continue
        size = body_size(pages, start, end - 1)
        for p in range(start, end):
            page_body[p] = size

    seq = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        pnum = pno + 1

        panels = []
        for d in page.get_drawings():
            if d["type"] not in ("f", "fs") or d["rect"].height < pcfg["panel_min_height"]:
                continue
            if not is_panel_fill(d.get("fill"), pcfg):
                continue
            r = pymupdf.Rect(d["rect"])
            cur = conn.execute(
                """INSERT INTO panels (book_id, page, x0, y0, x1, y1, fill, stroke)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (book_id, pnum, r.x0, r.y0, r.x1, r.y1,
                 json.dumps([round(c, 3) for c in d["fill"]]),
                 json.dumps([round(c, 3) for c in d["color"]]) if d.get("color") else None),
            )
            panels.append((r, cur.lastrowid))

        line_rows = []
        for ln in pages[pno]:
            bb = ln["bbox"]
            mid = pymupdf.Point((bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2)
            panel_id = next((pid for r, pid in panels if r.contains(mid)), None)
            i = bisect.bisect_right(start_keys, (pnum, bb.y0 + tol)) - 1
            section_id = ids[starts[i][2]] if i >= 0 else None
            body = page_body.get(pnum) or ln["size"]
            cur = conn.execute(
                """INSERT INTO lines (book_id, section_id, seq, page, x0, y0, x1, y1, size,
                   rel_size, bold_start, bold_frac, italic_frac, panel_id, text, spans_json, lkey)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (book_id, section_id, seq, pnum, bb.x0, bb.y0, bb.x1, bb.y1, ln["size"],
                 round(ln["size"] / body, 3), ln["bold_start"], round(ln["bold_frac"], 3),
                 round(ln["italic_frac"], 3), panel_id, ln["text"],
                 json.dumps(ln["spans"], ensure_ascii=False),
                 f"{pnum}:{round(bb.y0)}:{round(bb.x0)}"),
            )
            line_rows.append((bb, cur.lastrowid))
            seq += 1

        links = page.get_links()
        chars = page_chars(page) if links else []
        for link in links:
            r = pymupdf.Rect(link["from"])
            c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            line_id = next((lid for bb, lid in line_rows if bb.contains(c)), None)
            target = link["page"] + 1 if link.get("kind") == pymupdf.LINK_GOTO else None
            conn.execute(
                """INSERT INTO links (book_id, page, line_id, x0, y0, x1, y1, target_page, uri, text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (book_id, pnum, line_id, r.x0, r.y0, r.x1, r.y1, target,
                 link.get("uri"), chars_in(chars, r)),
            )

    conn.commit()
    return book_id
