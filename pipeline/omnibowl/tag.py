"""Parse step 3: tag every line with a block kind, then group lines into blocks.

Reads the measurement layer and the book's layout profile (profile.py). Nothing here
names a particular book's features; see docs/decisions.md ("parse: block tagging").

Regions. A recurring marker (from the profile) opens a region with the marker's role:
  - panel mode: the marker sits in a panel, or a container panel (role box, table or
    exercise) follows within `panel_search_lines`. The region ends with the panel.
  - open mode: otherwise. The region's content band is the size band of its first
    non-bold line. It ends at a heading, another marker, a new outline section, or,
    when the content is side-size, at the first body-size prose line.
Inside regions, item panels (caption, definition, objective) keep their own role, and
captions, drop caps and embedded definitions are tagged as such.
Outside regions, each line is tagged from its own features plus the previous kind.
"""

import json
import re
from dataclasses import dataclass

from . import db
from .profile import normalize

BOLD = 16
# Enumeration labels set on their own line: "(a)", "1.", "(12)", "iv)".
ENUM_RE = re.compile(r"^\(?(?:\d{1,3}|[a-z]|[ivx]{1,4})[.)]?\)?$")
CONTAINER_ROLES = {"box", "table", "exercise"}
ITEM_ROLES = {"caption", "definition", "objective"}


@dataclass
class Region:
    kind: str
    feature: str
    section_id: int | None
    mode: str = "search"  # search | panel | open
    since_marker: int = 0
    panel_seen: bool = False
    content_band: str | None = None
    in_definition: bool = False
    caption_line: object = None
    caption_left: float = 0.0
    prev_line: object = None


def starts_definition(ln) -> bool:
    """Bold leading term followed by a capitalized word, as in 'term Definition...'."""
    spans = json.loads(ln["spans_json"])
    i = 0
    while i < len(spans) and (spans[i][2] & BOLD or not spans[i][0].strip()):
        i += 1
    if i == 0:
        return False
    rest = "".join(s[0] for s in spans[i:]).lstrip()
    return bool(rest) and (rest[0].isupper() or rest[0] in "“\"(")


class Tagger:
    def __init__(self, cfg: dict, profile: dict, page_height: float = 0.0,
                 panel_fill: dict[int, list] | None = None):
        self.t = cfg["tag"]
        self.lo, self.hi = profile["sizes"]["body"]
        self.markers = {m["pattern"]: m["role"] for m in profile.get("markers", [])
                        if m["role"] != "heading"}
        self.panel_role = {json.dumps(s["fill"]): s["role"] for s in profile.get("panel_styles", [])}
        self.panel_fill = panel_fill or {}
        self.side_bold = profile["side_bold"]["role"]
        self.furn = profile["furniture"]
        self.page_height = page_height
        self.display_prose: set[int] = set()
        prefixes = cfg["profile"]["caption_prefixes"]
        self.caption = re.compile(rf"^(?:{'|'.join(map(re.escape, prefixes))})\s+\d")

    # ---- line features
    def band(self, rel: float) -> str:
        return "side" if rel < self.lo else "body" if rel <= self.hi else "head"

    def role_of_panel(self, ln) -> str | None:
        if ln["panel_id"] is None:
            return None
        return self.panel_role.get(json.dumps(self.panel_fill.get(ln["panel_id"])), "box")

    def is_furniture(self, ln) -> bool:
        if ENUM_RE.match(ln["text"].strip()):
            return True
        if not self.page_height:
            return False
        zone = self.furn["margin_zone"] * self.page_height
        if not (ln["y1"] <= zone or ln["y0"] >= self.page_height - zone):
            return False
        if self.furn["page_numbers"] and re.fullmatch(r"[0-9ivxlcIVXLC]+", ln["text"]):
            return True
        return normalize(ln["text"]) in self.furn["running_heads"]

    def find_display_prose(self, lines) -> None:
        """Runs of at least `display_prose_min_lines` consecutive non-bold above-body
        lines (page furniture ignored) are prose, not headings."""
        run: list[int] = []
        for ln in list(lines) + [None]:
            if ln is not None and self.is_furniture(ln):
                continue
            if (ln is not None and self.band(ln["rel_size"]) == "head"
                    and not ln["bold_start"] and not self.is_dropcap(ln)):
                run.append(ln["id"])
                continue
            if len(run) >= self.t["display_prose_min_lines"]:
                self.display_prose.update(run)
            run = []

    def is_dropcap(self, ln) -> bool:
        return self.band(ln["rel_size"]) == "head" and len(ln["text"]) == 1 and ln["text"].isalpha()

    def is_heading(self, ln) -> bool:
        """Above-body size, not a drop cap, and either bold or not part of a run of
        display prose (a chapter opener set large; titles are one or two lines)."""
        return (self.band(ln["rel_size"]) == "head" and not self.is_dropcap(ln)
                and (bool(ln["bold_start"]) or ln["id"] not in self.display_prose))

    def is_inline_heading(self, ln, prev_ln=None) -> bool:
        """Entirely bold, short, no sentence-ending punctuation, alone on its row."""
        text = ln["text"]
        if ln["bold_frac"] < self.t["inline_heading_min_bold"] or not any(c.isalpha() for c in text):
            return False
        if self.is_dropcap(ln) or self.is_captioned(ln):
            return False
        if len(text.split()) > self.t["inline_heading_max_words"] or text.rstrip()[-1] in ".?!:;,":
            return False
        return not (prev_ln is not None and prev_ln["page"] == ln["page"]
                    and abs(prev_ln["y0"] - ln["y0"]) <= self.t["same_row_tolerance"])

    def is_titled_caption(self, ln) -> bool:
        """A caption label line that also carries a title, entirely bold
        ("Figure 54.4 Predicting ..."), as opposed to a bare "Figure 5.3"."""
        return (self.is_captioned(ln) and ln["bold_frac"] >= self.t["inline_heading_min_bold"]
                and len(self.caption.sub("", ln["text"]).split()) > 1)

    def is_captioned(self, ln) -> bool:
        return bool(ln["bold_start"]) and bool(self.caption.match(ln["text"]))

    def marker(self, ln) -> Region | None:
        if not ln["bold_start"] or self.band(ln["rel_size"]) == "side":
            return None
        role = self.markers.get(normalize(ln["text"]))
        if role is None:
            return None
        r = Region(role, ln["text"], ln["section_id"])
        if ln["panel_id"] is not None:
            r.mode, r.panel_seen = "panel", True
        return r

    # ---- regions
    def in_region(self, r: Region, ln) -> str | None:
        """Kind for a line inside region r, or None if the line ends the region.

        Labels inside the region (title lines after the marker, entirely bold short
        lines, titled caption labels) are headings; the caller keeps the feature."""
        prev, r.prev_line = r.prev_line, ln
        kind = self._in_region(r, ln, prev)
        if kind in ("caption",) and self.is_titled_caption(ln):
            return "heading"
        return kind

    def _in_region(self, r: Region, ln, prev) -> str | None:
        r.since_marker += 1
        b = self.band(ln["rel_size"])
        if self.is_furniture(ln):
            return "furniture"
        prole = self.role_of_panel(ln)

        if r.mode == "search":
            if (self.is_heading(ln) and ln["bold_start"]) or self.marker(ln) \
                    or ln["section_id"] != r.section_id:
                return None
            if prole in CONTAINER_ROLES:
                r.mode, r.panel_seen = "panel", True
            elif r.since_marker > self.t["panel_search_lines"] or prole in ITEM_ROLES or (
                    not ln["bold_start"] and prole is None and b != "head"):
                r.mode = "open"
        if r.mode == "panel" and r.panel_seen and ln["panel_id"] is None:
            return None
        if r.mode == "search" and b == "head" and not ln["bold_start"] and not self.is_dropcap(ln):
            return "heading"  # title lines between the marker and the content
        if r.mode == "open":
            if self.is_heading(ln) or self.marker(ln) or ln["section_id"] != r.section_id:
                return None
            if prole in ITEM_ROLES:
                return prole
            if not ln["bold_start"] and r.content_band is None:
                r.content_band = b
            if r.content_band == "side" and b == "body" and not ln["bold_start"] and " " in ln["text"]:
                return None
            if r.kind == "exercise" and self.is_captioned(ln):
                return None

        if r.kind != "table" and prole not in ITEM_ROLES and self.is_inline_heading(ln, prev):
            return "heading"
        if r.kind == "exercise":
            return r.kind
        if r.kind in ("box", "summary", "body") and (self.is_captioned(ln) or self.continues_caption(r, ln)):
            if self.is_captioned(ln):
                r.caption_left = ln["x0"]
            r.caption_line = ln
            return "caption"
        r.caption_line = None
        if self.is_dropcap(ln):
            return "dropcap"
        # Side-size essays: an embedded definition starts with a bold term followed by a
        # capitalized word and runs to the first line that ends a sentence.
        if r.mode == "open" and b == "side" and ln["panel_id"] is None and starts_definition(ln):
            r.in_definition = True
        if r.in_definition:
            r.in_definition = not ln["text"].endswith(".")
            return "definition"
        return r.kind

    def continues_caption(self, r: Region, ln) -> bool:
        """Caption text runs on until a line indented past the caption's left edge (the
        next paragraph) or a change of size band."""
        c = r.caption_line
        if c is None or ln["page"] != c["page"] or self.band(ln["rel_size"]) != self.band(c["rel_size"]):
            return False
        if abs(ln["y0"] - c["y0"]) <= self.t["same_row_tolerance"]:
            return True
        return ln["x0"] <= r.caption_left + self.t["caption_continue_indent"]

    # ---- outside regions
    def outside(self, ln, prev) -> str:
        """prev is (kind, line, block_left_x) for the previous line outside regions."""
        b = self.band(ln["rel_size"])
        text = ln["text"]
        if self.is_furniture(ln):
            return "furniture"
        if prev is not None and abs(ln["y0"] - prev[1]["y0"]) <= self.t["same_row_tolerance"] \
                and ln["page"] == prev[1]["page"] and b == self.band(prev[1]["rel_size"]):
            return prev[0]
        if self.is_titled_caption(ln):
            return "heading"
        prole = self.role_of_panel(ln)
        if prole is not None:
            return prole
        if self.is_dropcap(ln):
            return "dropcap"
        if b == "head":
            return "heading" if self.is_heading(ln) else "body"  # long lines: opener prose
        if b == "side":
            if ln["bold_start"]:
                return "caption" if self.caption.match(text) else self.side_bold
            if prev and prev[0] in ("caption", "definition") \
                    and ln["x0"] - prev[2] < self.t["example_min_indent"]:
                return prev[0]
            return "example"
        if not any(ch.isalnum() for ch in text):
            return "furniture"
        if self.is_inline_heading(ln, prev[1] if prev else None):
            return "heading"
        return "body"


def run(book_id: int, conn, cfg: dict, profile: dict) -> None:
    book = conn.execute("SELECT page_height FROM books WHERE id = ?", (book_id,)).fetchone()
    panel_fill = {r["id"]: json.loads(r["fill"])
                  for r in conn.execute("SELECT id, fill FROM panels WHERE book_id = ?", (book_id,))}
    tagger = Tagger(cfg, profile, book["page_height"] or 0.0, panel_fill)
    db.clear(conn, book_id, db.TAG_TABLES)

    secs = conn.execute("SELECT id, parent_id, kind FROM sections WHERE book_id = ?", (book_id,)).fetchall()
    parent = {s["id"]: s["parent_id"] for s in secs}
    kinds = {s["id"]: s["kind"] for s in secs}

    def top(sid):
        while sid is not None and parent.get(sid) is not None:
            sid = parent[sid]
        return sid

    lines = conn.execute("SELECT * FROM lines WHERE book_id = ? ORDER BY seq", (book_id,)).fetchall()
    tagger.find_display_prose(lines)
    tags: list[tuple[str, str | None, int]] = []
    region: Region | None = None
    prev = None
    for ln in lines:
        top_kind = kinds.get(top(ln["section_id"]), "front_matter")
        if top_kind in ("front_matter", "back_matter"):
            tags.append((top_kind, None, ln["id"]))
            region, prev = None, None
            continue
        if (m := tagger.marker(ln)) is not None:
            region = m
            tags.append(("heading", m.feature, ln["id"]))  # the marker names the region
            continue
        if region is not None:
            kind = tagger.in_region(region, ln)
            if kind is not None:
                tags.append((kind, region.feature, ln["id"]))
                continue
            region = None
        kind = tagger.outside(ln, prev)
        continuing = prev and kind == prev[0] and kind in ("caption", "definition") and not ln["bold_start"]
        prev = (kind, ln, prev[2] if continuing else ln["x0"])
        tags.append((kind, None, ln["id"]))

    conn.executemany("UPDATE lines SET kind = ?, feature = ? WHERE id = ?", tags)
    _write_blocks(book_id, conn)
    conn.commit()


def _write_blocks(book_id: int, conn) -> None:
    """Blocks: maximal runs of (kind, feature, section) in reading order; each bold-start
    caption or definition line begins a new block."""
    rows = conn.execute(
        "SELECT id, seq, page, section_id, kind, feature, bold_start FROM lines "
        "WHERE book_id = ? ORDER BY seq", (book_id,)).fetchall()
    run_rows: list = []
    updates = []

    def flush():
        if not run_rows:
            return
        f, l = run_rows[0], run_rows[-1]
        cur = conn.execute(
            """INSERT INTO blocks (book_id, section_id, kind, feature, seq_start, seq_end,
               page_start, page_end) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (book_id, f["section_id"], f["kind"], f["feature"], f["seq"], l["seq"],
             f["page"], l["page"]))
        updates.extend((cur.lastrowid, r["id"]) for r in run_rows)
        run_rows.clear()

    for r in rows:
        new_item = r["kind"] in ("caption", "definition") and r["bold_start"]
        if run_rows and (new_item or (r["kind"], r["feature"], r["section_id"]) != (
                run_rows[0]["kind"], run_rows[0]["feature"], run_rows[0]["section_id"])):
            flush()
        run_rows.append(r)
    flush()
    conn.executemany("UPDATE lines SET block_id = ? WHERE id = ?", updates)
