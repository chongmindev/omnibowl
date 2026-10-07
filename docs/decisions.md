# Decisions

One entry per algorithm decision: what was chosen, what was rejected, and why.

## parse: per-chapter size normalization (2026-10-06)

Measured, not chosen: chapters 9 and 11 are scaled versions of the others, so fixed
point-size cutoffs would misclassify two chapters. `lines.rel_size` divides by the
modal non-bold size (by characters) of each level-1 outline range.

## parse: box end detection (2026-10-06)

**Chosen: C, panel first with a size fallback.** A content-box marker (BOX, LANGUAGE AT
LARGE, METHOD, RESEARCHERS AT WORK) opens a region; title lines before the panel belong
to it; the box ends at the first line outside the panel once the panel has started. If
no panel appears within `box_panel_search_lines`, the box ends at the first body-size,
non-bold line.

Rejected: A (panel only) is exact here (120/120 markers have panels) but fails on any
book without box backgrounds. B (size only) cuts boxes short because boxes hold
body-size examples and table cells.

## parse: block tagging (2026-10-06)

**Chosen: C, an ordered rule cascade plus a labeled eval set** (`eval/parse_tags.tsv`,
100 lines: 60 uniform, 40 at non-body block boundaries; `eval/tag_accuracy.py`).

Rejected: B (weighted score per block): more knobs than the signals need; the rules
separate cleanly once sizes are normalized. A alone: no measurement.

Rules, in order (see `pipeline/omnibowl/tag.py`):
1. Marker lines open regions: content box (panel), table (panel), essay (DIGGING
   DEEPER, runs to next marker or heading), exercise (WEB ACTIVITY, GO TO, PROJECT,
   Questions to Contemplate; ends at heading, marker, caption, or body prose with a
   space, so URL fragments stay inside).
2. Inside boxes: "Figure/Table N.M" bold starts a caption, which runs on at the label's
   left edge and stops at the box's indented next paragraph. Drop caps are their own kind.
3. Inside essays: a bold term followed by a capitalized word starts an embedded
   definition, which runs to the first line ending in a period. Prose that merely opens
   with a bold key term continues lowercase and stays essay text.
4. Outside regions: title band -> drop cap (one letter) or heading; head band ->
   heading; subhead band -> heading if bold, bullet furniture, else opener prose (body);
   side band -> caption (bold "Figure N.M"), definition (other bold start), continuation
   of the previous caption/definition (indent < `example_min_indent`), else example;
   body band -> body. A line on the same row as the previous line inherits its kind.

Results: baseline 98/100; after fixing in-box drop caps and in-box caption continuation,
100/100. Both fixes came from errors on this set, so 100% is optimistic; draw a fresh
sample to measure held-out accuracy.

Known gap (open): body-size numbered examples and stimulus sentences indented about
30 pt are tagged body. Bullet items and long block quotes share that indent.

## parse: generalize to any textbook (2026-10-07)

Chong's direction: rules must apply to any textbook; the v0 book is a test case. Second
test book: Myers, *Psychology for the AP Course* (3rd ed.), a Calibre-converted ebook
with a 3-level outline (Unit > Module > section), definitions and objectives in
panels, unlabeled photo captions, page-number footers, and a two-column glossary.

**Structure is read from the outline's shape, not titles.** A top-level entry is
content if it or a descendant is numbered ("3", "3.1", "Unit IV", "Module 12");
unnumbered top-level entries before or after the content are front or back matter.
A label also used with plain letters ("Appendix A") is lettering, not Roman numerals.
Outline destinations that point to the top of a page are moved to the heading line on
that page whose text starts the title (or equals the number).

**A profiling step discovers per-book layout** (`profile.py`, written to
`data/profiles/<book>.auto.toml`; corrections in `<book>.toml` override it):

- Size bands, **option A (chosen): histogram peaks.** Body band runs halfway to the
  nearest peak on each side. Rejected B (clustering on size, bold, indent, spacing):
  harder to inspect, more tuning. Myers' side stream is 0.9x body versus Sedivy's 0.75x,
  so copied bands would have failed.
- Feature roles, **option C (chosen): automatic guess, then human review.** Markers are
  recurring short bold lines (digits and Roman numerals normalized), seen at least
  `marker_min_count` times in `marker_min_chapters` chapters; inside a panel only the
  panel's first line counts. Role comes from an English textbook word list in config
  (question, practice, review, target, table, ...), else from the content the tagger
  would put in the region (questions, URLs, side size, panels), else "heading" (a
  recurring heading over ordinary prose). Rejected A alone (no review step) and B
  alone (manual tagging per book).
- Panel styles are grouped by fill color with a role guessed from their contents:
  caption (starts "Figure N"), table (short cells in several x-columns), objective
  (short, questions or bold throughout), definition (short, bold term then plain
  text), else box.
- Bold side-size lines outside panels are definitions if most start lowercase (Sedivy
  glossary terms) and captions otherwise (Myers photo captions of people).
- Furniture: page numbers and running heads repeated in the top/bottom margin.

Regions now have three modes: panel (marker in or followed by a container panel), open
(ends at a bold heading, marker, new section, or a return to body size after side-size
content), and search (the lines in between). Two-column pages are read column by column.

Results (eval/parse_tags.tsv): Sedivy 100/100 (tuned on this set, optimistic). Myers
45/50 on first run (labels drawn after the tagger was written, so a fair baseline),
46/50 after fixing section starts. Remaining Myers misses need rules that do not exist
yet: text inside diagrams, tables without panels, captions without a "Figure" label,
and a body line set in a smaller font.

## parse: labels review and heading rules (2026-10-06)

Chong reviewed the 150 eval labels. Decisions from the review:

- **Labels name the line's role, not its look.** A line that names a region (a feature
  marker such as "BOX 3.1", "Module 51 REVIEW", "AP® EXAM TIP"; title lines right after
  it; an entirely bold short line inside a box such as "Linguistic performance" or
  "Answer") is a `heading`, with `feature` still naming the region. Reason: a label is
  not a statement, and tagging it with the region's kind would glue it onto the first
  sentence of the region. Run-in bold labels followed by text on the same line stay
  with the region; bold terms in definition panels stay `definition`; table cells are
  excluded.
- **Caption title lines** are headings only when the title sits on its own entirely
  bold line ("Figure 54.4 Predicting ..."); a bare "Figure 5.3" label whose text
  continues on the same row stays `caption`. Rejected: all figure labels as headings;
  none.
- **Chapter-end essays (Sedivy's DIGGING DEEPER) are `body`**, with `feature` kept so
  `rank` can still see where a clue came from. Rejected: plain body without the
  feature; keeping them as `box`. Set by hand in `profiles/language_in_mind.toml`
  (marker role `body`).
- **Check Your Understanding stays `exercise`** (questions, not facts). Its self-test
  questions may later serve `rank` as an emphasis signal.
- **Hand corrections to profiles are committed** in `profiles/<book>.toml`; generated
  profiles stay in `data/profiles/`. Rejected: keeping them local.

Eval after review and rules: Sedivy 100/100, Myers 46/50 (97.3% overall). Remaining
misses are text inside figures and charts, tables without panels, unlabeled cartoon
captions, and a body line in a smaller font. Text inside figures also produces false
headings (bold axis labels such as "Height in Inches"); not yet handled.

Fixed in the review tool: crops on a page with two sampled lines showed both outlines.

## parse: sentences (2026-10-06)

**Splitter: spaCy (chosen),** `en_core_web_sm`, dependency parser for boundaries.
Rejected: regex rules (an abbreviation list that grows with every book); spaCy plus
correction rules (Chong chose plain spaCy; rules can be added where the eval shows
failures). spaCy is also needed by `candidates` for noun phrases and entities.

Stitching before splitting (plumbing, in `sentences.py`):
- Units: body prose of a section continues across interruptions (definitions,
  captions, boxes placed mid-paragraph) and breaks at a plain heading or a new
  section; a region's text breaks at its own headings; each definition or caption
  starts at its bold line, so page furniture inside a panel cannot split it.
- Drop caps join the first line of their stream.
- Line-final hyphen: kept unless the book uses the joined word more often than the
  hyphenated form. Both test books are reflowed ebooks whose line-final hyphens are
  all real compounds; a print-layout PDF would hyphenate ordinary words. Deciding from
  the book's own vocabulary covers both.
- A definition's bold term always stays with the sentence after it (spaCy splits
  "phonetics The study of..." and "placebo [pluh-SEE-bo] effect ...").

Tagging fixes found while stitching: a run of 3+ non-bold above-body lines is display
prose (chapter openers), not headings (`display_prose_min_lines`, replacing the
character-length rule); enumeration labels on their own line ("(a)", "12.") are
furniture.

Eval (`eval/sentences.tsv`, 50 judged sentences): 45/50. Sedivy 29/30, Myers 16/20.
Errors: splits inside citations after ";" (2), run-in caption titles fused with the
first sentence (2), a split after initials in a reference (1).

**Correction rules (2026-10-07, Chong chose A and B):**
- A: never end a sentence inside unclosed parentheses or brackets.
- B: a caption's bold run-in label ("Figure 2.1", "Culture and normality") is dropped
  from its first sentence, like a heading; a bold opening that is itself a sentence
  is kept.
- C (deferred): no split right before "(year)" after initials; one case so far.

After A and B: 49/50 (Sedivy 29/30, Myers 20/20). Tagging unchanged at 146/150.
