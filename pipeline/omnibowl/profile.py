"""Parse step 2: profile a book's layout so tagging needs no book-specific rules.

Reads only the measurement layer (lines, panels, sections) and writes
data/profiles/<slug>.auto.toml, regenerated on every run. Corrections go in
profiles/<slug>.toml (committed), which is never overwritten and overrides the auto
profile key by key (sizes and furniture by key, markers by pattern, panel styles by
fill).

What is discovered:
  sizes        char-weighted peaks of rel_size; body band = halfway to the nearest
               peaks on each side (decision: size bands option A).
  furniture    page numbers and running heads in the top/bottom margin.
  panels       light-filled panel styles grouped by fill, with a guessed role.
  markers      recurring short bold lines (numbers normalized away) with a guessed
               role (decision: feature roles option C, guess then human review).
  side_bold    what a bold side-stream line outside panels is: definition or caption.
"""

import json
import re
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

from . import config

ROMAN_RE = re.compile(r"(?<=\s)[IVXLC]+(?![\w])")
NUM_RE = re.compile(r"\d+")
URL_RE = re.compile(r"https?://|www\.|\.com\b|\.org\b")
ROLES = ("body", "box", "exercise", "objective", "summary", "table", "caption", "definition", "heading")


def normalize(text: str) -> str:
    """Marker pattern: digits -> N, Roman numerals after a word -> R, trailing digits
    glued to a word (footnote marks) dropped."""
    t = re.sub(r"(?<=[A-Za-z])\d+$", "", text.strip())
    t = NUM_RE.sub("N", t)
    return ROMAN_RE.sub("R", t)


def profile_paths(slug: str, cfg: dict) -> tuple[Path, Path]:
    return (config.ROOT / cfg["paths"]["profiles"] / f"{slug}.auto.toml",
            config.ROOT / cfg["paths"]["profile_overrides"] / f"{slug}.toml")


# ---------------------------------------------------------------------------- sizes

def size_peaks(lines, pcfg) -> list[tuple[float, float]]:
    chars = Counter()
    for ln in lines:
        chars[round(ln["rel_size"] / pcfg["size_bin"]) * pcfg["size_bin"]] += len(ln["text"])
    total = sum(chars.values()) or 1
    return sorted((round(s, 3), round(n / total, 4)) for s, n in chars.items()
                  if n / total >= pcfg["min_peak_share"])


def size_bands(peaks: list[tuple[float, float]]) -> dict:
    body = max(peaks, key=lambda p: p[1])[0]
    below = [s for s, _ in peaks if s < body]
    above = [s for s, _ in peaks if s > body]
    lo = (body + max(below)) / 2 if below else 0.0
    hi = (body + min(above)) / 2 if above else 99.0
    return {"body": [round(lo, 3), round(hi, 3)]}


# ------------------------------------------------------------------------ furniture

def furniture(lines, page_height: float, n_pages: int, pcfg) -> dict:
    zone = pcfg["margin_zone"] * page_height
    in_zone = [ln for ln in lines if ln["y1"] <= zone or ln["y0"] >= page_height - zone]
    num_pages = {ln["page"] for ln in in_zone if re.fullmatch(r"[0-9ivxlcIVXLC]+", ln["text"])}
    heads = defaultdict(set)
    for ln in in_zone:
        if not re.fullmatch(r"[0-9ivxlcIVXLC]+", ln["text"]):
            heads[normalize(ln["text"])].add(ln["page"])
    need = pcfg["furniture_min_page_share"] * n_pages
    return {"page_numbers": len(num_pages) >= need,
            "running_heads": sorted(t for t, pages in heads.items() if len(pages) >= need),
            "margin_zone": pcfg["margin_zone"]}


# --------------------------------------------------------------------------- panels

def panel_styles(lines, panels, pcfg, caption_re) -> list[dict]:
    fill_of = {p["id"]: p["fill"] for p in panels}
    by_panel = defaultdict(list)
    for ln in lines:
        if ln["panel_id"] is not None:
            by_panel[ln["panel_id"]].append(ln)
    styles = defaultdict(list)
    for pid, ls in by_panel.items():
        styles[fill_of[pid]].append(ls)
    out = []
    for fill, groups in styles.items():
        n_lines = sum(len(g) for g in groups)
        if n_lines < pcfg["panel_min_lines"]:
            continue
        first = [g[0] for g in groups]
        cap = sum(bool(caption_re.match(f["text"])) for f in first) / len(groups)
        bold_first = sum(f["bold_start"] for f in first) / len(groups)
        bold_lines = sum(l["bold_start"] for g in groups for l in g) / n_lines
        q = sum(l["text"].rstrip().endswith("?") for g in groups for l in g) / n_lines
        mean_len = n_lines / len(groups)
        xs = Counter(round(l["x0"] / 10) for g in groups for l in g)
        x_spread = len([x for x, c in xs.items() if c >= pcfg["table_x_min_share"] * n_lines])
        short = sum(len(l["text"]) <= pcfg["table_cell_max_chars"] for g in groups for l in g) / n_lines
        if cap >= pcfg["role_share"]:
            role = "caption"
        elif short >= pcfg["role_share"] and x_spread >= pcfg["table_min_columns"]:
            role = "table"
        elif q >= pcfg["question_share"] or (
                bold_lines >= pcfg["role_share"] and mean_len <= pcfg["objective_max_lines"]):
            # Short panels that are questions, or bold throughout, state goals.
            role = "objective" if mean_len <= pcfg["objective_max_lines"] else "exercise"
        elif bold_first >= pcfg["role_share"] and mean_len <= pcfg["definition_max_lines"]:
            role = "definition"  # a bold term, then plain definition text
        else:
            role = "box"
        out.append({"fill": json.loads(fill), "role": role, "instances": len(groups),
                    "lines": n_lines,
                    "evidence": (f"caption-first {cap:.0%}, bold-first {bold_first:.0%}, bold lines {bold_lines:.0%}, "
                                 f"questions {q:.0%}, short {short:.0%}, columns {x_spread}, "
                                 f"mean {mean_len:.1f} lines")})
    return sorted(out, key=lambda s: -s["lines"])


# -------------------------------------------------------------------------- markers

def band(rel: float, bands: dict) -> str:
    lo, hi = bands["body"]
    return "side" if rel < lo else "body" if rel <= hi else "head"


def markers(lines, tops: dict, bands: dict, pcfg, caption_re, lexicon: dict) -> list[dict]:
    hits = defaultdict(list)
    panel_first = {}
    for ln in lines:
        if ln["panel_id"] is not None:
            panel_first.setdefault(ln["panel_id"], ln["id"])
    for i, ln in enumerate(lines):
        t = ln["text"]
        # Inside a panel, only the panel's first line can be a marker (a panel title);
        # other bold lines there are table headers or list items.
        if ln["panel_id"] is not None and panel_first[ln["panel_id"]] != ln["id"]:
            continue
        if (not ln["bold_start"] or band(ln["rel_size"], bands) == "side"
                or len(t.split()) > pcfg["marker_max_words"] or caption_re.match(t)):
            continue
        pat = normalize(t)
        if not re.search(r"[A-Za-z]{2}", pat.replace("N", "").replace("R", "")):
            continue
        hits[pat].append(i)
    out = []
    for pat, idx in hits.items():
        chapters = {tops.get(lines[i]["section_id"]) for i in idx}
        if len(idx) < pcfg["marker_min_count"] or len(chapters) < pcfg["marker_min_chapters"]:
            continue
        # Content: what the tagger would put in the region. The content band is the size
        # band of the first non-bold line; sampling stops when the band changes (outside
        # panels) or at the next bold line above side size.
        content = []
        for i in idx:
            cband = None
            for ln in lines[i + 1:i + 1 + pcfg["marker_sample_lines"]]:
                b = band(ln["rel_size"], bands)
                dropcap = len(ln["text"]) == 1 and ln["text"].isalpha()
                if dropcap:
                    continue
                if ln["bold_start"] and b != "side" and not ln["panel_id"]:
                    break
                if cband is None and not ln["bold_start"] and b != "head":
                    cband = b  # title lines (head band) don't set the content band
                if cband is not None and b != cband and not ln["panel_id"]:
                    break
                content.append(ln)
        n = len(content) or 1
        q = sum(l["text"].rstrip().endswith("?") for l in content) / n
        url = sum(bool(URL_RE.search(l["text"])) for l in content) / n
        side = sum(band(l["rel_size"], bands) == "side" for l in content) / n
        in_panel = sum(l["panel_id"] is not None for l in content) / n
        words = set(re.findall(r"[a-z]+", pat.lower()))
        role = next((r for r, ws in lexicon.items() if words & set(ws)), None)
        if role is None:
            if q >= pcfg["question_share"] or url >= pcfg["url_share"]:
                role = "exercise"
            elif side >= pcfg["role_share"] or in_panel >= pcfg["role_share"]:
                role = "box"
            else:
                role = "heading"  # recurring heading over ordinary body prose
        out.append({"pattern": pat, "role": role, "count": len(idx), "chapters": len(chapters),
                    "evidence": (f"after it: questions {q:.0%}, urls {url:.0%}, side {side:.0%}, "
                                 f"in panel {in_panel:.0%}"
                                 + (f"; lexicon '{', '.join(sorted(words & set(lexicon.get(role, []))))}'"
                                    if role in lexicon and words & set(lexicon[role]) else ""))})
    return sorted(out, key=lambda m: -m["count"])


def side_bold_role(lines, bands, pcfg) -> dict:
    """Bold side lines outside panels: lowercase leading terms read as glossary
    definitions ('nativist view The view...'); capitalized ones as captions of people
    or photos ('B. F. Skinner (1904-1990) ...')."""
    cands = [ln for ln in lines if ln["bold_start"] and ln["panel_id"] is None
             and band(ln["rel_size"], bands) == "side"]
    lower = sum(ln["text"][:1].islower() for ln in cands) / (len(cands) or 1)
    role = "definition" if lower >= pcfg["role_share"] else "caption"
    return {"role": role, "evidence": f"{len(cands)} lines, lowercase start {lower:.0%}"}


# ---------------------------------------------------------------------------- build

def build(book_id: int, conn, cfg: dict) -> dict:
    pcfg = cfg["profile"]
    caption_re = re.compile(rf"^(?:{'|'.join(map(re.escape, pcfg['caption_prefixes']))})\s+\d")
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    secs = conn.execute("SELECT id, parent_id, kind FROM sections WHERE book_id = ?", (book_id,)).fetchall()
    parent = {s["id"]: s["parent_id"] for s in secs}
    kind = {s["id"]: s["kind"] for s in secs}

    def top(sid):
        while sid is not None and parent.get(sid) is not None:
            sid = parent[sid]
        return sid

    tops = {s["id"]: top(s["id"]) for s in secs}
    all_lines = conn.execute("SELECT * FROM lines WHERE book_id = ? ORDER BY seq", (book_id,)).fetchall()
    lines = [ln for ln in all_lines if kind.get(tops.get(ln["section_id"])) == "chapter"]
    panels = conn.execute("SELECT id, fill FROM panels WHERE book_id = ?", (book_id,)).fetchall()
    content_pages = {ln["page"] for ln in lines}

    peaks = size_peaks(lines, pcfg)
    bands = size_bands(peaks)
    return {
        "book": book["slug"],
        "sizes": {**bands, "peaks": [list(p) for p in peaks]},
        "furniture": furniture(lines, book["page_height"], len(content_pages), pcfg),
        "side_bold": side_bold_role(lines, bands, pcfg),
        "panel_styles": panel_styles(lines, panels, pcfg, caption_re),
        "markers": markers(lines, tops, bands, pcfg, caption_re, pcfg["role_lexicon"]),
    }


# ----------------------------------------------------------------------------- toml

def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return "[" + ", ".join(_val(x) for x in v) + "]"


def to_toml(p: dict) -> str:
    out = [f"# Auto-generated layout profile for {p['book']}. Regenerated on every run;",
           f"# put corrections in profiles/{p['book']}.toml (same keys; markers by pattern, panel",
           "# styles by fill). Roles: " + ", ".join(ROLES) + ".", "",
           f"book = {_val(p['book'])}", ""]
    for table in ("sizes", "furniture", "side_bold"):
        out.append(f"[{table}]")
        out += [f"{k} = {_val(v)}" for k, v in p[table].items()]
        out.append("")
    for arr in ("panel_styles", "markers"):
        for item in p[arr]:
            out.append(f"[[{arr}]]")
            out += [f"{k} = {_val(v)}" for k, v in item.items()]
            out.append("")
    return "\n".join(out)


def merge(auto: dict, override: dict) -> dict:
    p = {k: (dict(v) if isinstance(v, dict) else [dict(x) for x in v] if isinstance(v, list) else v)
         for k, v in auto.items()}
    for table in ("sizes", "furniture", "side_bold"):
        p[table].update(override.get(table, {}))
    for arr, key in (("markers", "pattern"), ("panel_styles", "fill")):
        index = {json.dumps(x[key]): x for x in p[arr]}
        for o in override.get(arr, []):
            if json.dumps(o[key]) in index:
                index[json.dumps(o[key])].update(o)
            else:
                p[arr].append(dict(o))
    return p


def run(book_id: int, conn, cfg: dict) -> dict:
    p = build(book_id, conn, cfg)
    auto_path, override_path = profile_paths(p["book"], cfg)
    auto_path.parent.mkdir(parents=True, exist_ok=True)
    auto_path.write_text(to_toml(p))
    return load(p["book"], cfg)


def load(slug: str, cfg: dict) -> dict:
    auto_path, override_path = profile_paths(slug, cfg)
    with open(auto_path, "rb") as f:
        p = tomllib.load(f)
    if override_path.exists():
        with open(override_path, "rb") as f:
            p = merge(p, tomllib.load(f))
    return p
