# Stage: parse

## What exists

`parse.run` is a measurement layer. It writes, per book:

- `sections`: outline entries (chapter, section, front/back matter) with the outline
  destination `(page_start, y_start)`, so a section can start mid-page.
- `lines`: every text line in reading order (`seq`), with page, bbox, modal size,
  `rel_size` (size / body size of the enclosing level-1 outline entry), bold and italic
  shares, the containing light-filled `panel_id`, and the raw spans as JSON.
- `panels`: light-filled vector rectangles (box and table backgrounds).
- `links`: internal links (target PDF page) and URIs, with the text under the link and
  the line it sits on.

No line is classified yet. Block tagging and sentence splitting read these tables.

## Measurements on the v0 book (2026-10-06)

Counts and layout facts only; no book text.

- 1,279 pages; outline 86 entries = 8 front matter, 13 chapters, 61 sections, 4 back
  matter. Only one outline title had mojibake; cp1252 round-trip repairs it.
- **Chapters are scaled differently.** Chapter 9 is about 0.83x (body 12.5 pt, side
  9.4 pt) and chapter 11 about 0.99x (14.9 / 11.2). All others are 15 / 11.3. Ratios are
  constant, so all size logic must use `rel_size`, not points. Normalized clusters:
  1.0 body, 0.75 side stream, 1.13 subsection heading (and chapter-opener prose),
  1.41 section heading / box title / feature marker, 2.25 drop cap.
- **Panels.** The ebook was cut from long per-chapter scrolls, so a box background is one
  rectangle whose coordinates run past the page edge; the same panel is redrawn on every
  page it covers. All 120 content features (63 BOX, 27 LANGUAGE AT LARGE, 18 METHOD,
  12 RESEARCHERS AT WORK) are followed by text inside a light panel. Fill color
  distinguishes feature type; gray panels are tables.
- **Exercise features** (84 WEB ACTIVITY, 13 GO TO, 12 PROJECT, Questions to
  Contemplate) have no light panel. WEB ACTIVITY is a stroked frame with a dark header
  bar holding a title, one sentence, and a URL.
- **DIGGING DEEPER** (12, one per chapter end) has no panel. It is a full side-size
  essay at chapter end, not an exercise.
- Side-size lines in chapters: 6,032 inside panels, 4,011 outside (margin definitions,
  captions, Questions to Contemplate, Digging Deeper, numbered examples).
- Links: 276 chapter -> Glossary, 906 chapter -> Literature Cited, ~3,960 subject index,
  ~1,150 author index, 255 glossary -> introduction page. 212 lines start with a bold
  "Figure N.M" / "Table N.M".
- One multi-line section heading (9.3) puts the number between two title lines.

## Performance

About 50 s for the whole book. `page.get_textbox` re-extracts the page per call and
cost 90% of runtime; link text now comes from one `rawdict` pass per page.
