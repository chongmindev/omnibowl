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

## parse: glossary and indexes as labels (2026-10-07)

Back-matter parts are identified by content, using the same guess-then-review approach
as features (corrections in `profiles/<book>.toml`, by part title):
- index: at least half its lines carry page links; author index when most linked
  lines look like "Surname, X.", else subject index;
- glossary: enough short bold terms (no digits, at most 6 words) of which at least 40%
  occur in the chapters. Bold-start share alone misread Myers' references and answer
  key as glossaries;
- everything else: other (references, appendices).

Extraction (`backmatter.py`) writes `labels`: glossary terms with definitions and the
introduction page; index entries with linked pages, sub-entries under their parent
(indent past the leftmost common left edge), wrapped page lists joined. `in_text`
marks terms found in the chapters (for authors, the surname), which filters the
Spanish half of Myers' bilingual glossary.

Results: Sedivy 275 glossary terms (all in text), 2,441 subject index entries (1,336
sub-entries), 796 authors (779 found). Myers 1,361 glossary entries (715 in text),
8,049 subject index entries, 438 authors. One hand correction: Myers' Appendix E (an
answer key) set to `other`.

## candidates: extraction (2026-10-07)

**Chosen: C.** Concepts from part-of-speech patterns (Justeson and Katz: adjectives and
nouns ending in a noun, optional "noun of noun", hyphenated compounds and possessive 's
inside, nested matches kept, up to 5 words); people from spaCy PERSON entities grouped
by surname. Rejected: A (spaCy noun chunks: run long, split terms), B alone (misses
people).

**Single words allowed** (2026-10-07): 34% of Sedivy's and 44% of Myers' glossary terms
are single words; keyness is meant to filter common ones. CLAUDE.md updated.

Recall before scoring (Sedivy): glossary 246/275 (89.5%), subject index 76.4%,
author index 26.7%. Glossary misses: adjective terms (bilabial, velar), terms over 5
words, rare terms tagged inconsistently, "-ing" nouns tagged as verbs. Authors are
weak because spaCy misses surnames in citations; citation patterns are a proposed fix.

## candidates: keyness (2026-10-07/08)

**Chosen: A, smoothed log ratio** against everyday English (`wordfreq`, approved with
this choice), for one-word terms and people: log2((book per million + 1) / (English per
million + 1)), Kilgarriff's "simple maths". Rejected: B, log-likelihood (no intuitive
scale); C, C-value (cannot tell common words from technical ones, ignores single words).
Closest real system: Sketch Engine term extraction (POS patterns + smoothed ratio).

**Phrases: option 3, scored inside the book.** wordfreq's phrase estimate is built from
word frequencies ("garden path sentence" 15.9 per million), so multi-word terms would all
look ordinary. Rejected: 1 (that estimate), 2 (independence estimate: every phrase key).
Within option 3, plain PMI ranked worse than mention counts (it rewards phrases seen
twice); **local mutual information (mentions x PMI, Evert 2005)** was chosen over
PMI x log2(mentions) (tied below the top 100, LMI best at the top).

A whole-word stoplist of boilerplate ("et al", "other hand", ...) in config.

Precision at k against glossary + index labels (a lower bound: unlabeled real terms
count as misses):

| | Sedivy P@50 / P@100 / P@500 | Myers P@50 / P@100 / P@500 |
|---|---|---|
| one-word, by mentions (baseline) | 28 / 17 / 15 | 40 / 38 / 27 |
| one-word, log ratio | 40 / 35 / 25 | 58 / 46 / 36 |
| multi-word, by mentions (baseline) | 34 / 37 / 22 | 44 / 48 / 37 |
| multi-word, PMI | 20 / 16 / 13 | 8 / 9 / 11 |
| multi-word, local MI | 58 / 46 / 26 | 52 / 54 / 43 |

## candidates: coverage, classes, variants, citations (2026-10-08)

- **Coverage: B** (counts plus spread). `coverage` = distinct sentences; `n_chapters`
  and `spread` = how many chapters mention the term. Rejected: A (counts only, cannot
  see "too broad"); C (only sentences about the term: that is the clues stage's job).
- **Class lines: X** (hand-set; tune against labels, option Y, only if these fail).
  Kept the strict setting **4 / 100** after comparing 3.5 / 75 and 3 / 50:

  | lines | Sedivy answers / labeled / glossary recall | Myers |
  |---|---|---|
  | 4 / 100 (chosen) | 519 / 36% / 28% | 669 / 46% / 32% |
  | 3.5 / 75 | 759 / 31% / 33% | 1,009 / 41% / 43% |
  | 3 / 50 | 1,052 / 26% / 39% | 1,478 / 37% / 56% |

  Classes: too_broad (10+ of 13 chapters), answer (5+ sentences, log ratio >= 4 or
  local MI >= 100), clue_only (above the floor 2 / 50), not_term (new; below the floor,
  so everyday words are not called clue_only). Revisit once `assemble` shows which
  answers make good tossups (near-misses: Ganong effect, lexical bias, natural classes).
- **Variant grouping: A + B.** Same content words in any order or form, plus synonym
  heads from config ("color terms" ~ "color words" ~ "color names"). Rejected for now:
  C (shared-context clustering; could merge N400 and P600).
- **Common-link tossups** ("blue": "shades of blue", "the blue divide") are a planned
  separate format after regular tossups work (CLAUDE.md, Scope). Grouping does not
  conflict: it merges synonyms of one concept; link answers keep shared words separate.
- **Citation authors** added as a person source ("Saffran et al., 1996"): author recall
  27% -> 54% (Sedivy), 22% -> 43% (Myers).
- Extraction fixes: phrases opening with determiner-like adjectives dropped ("other
  languages"); phrases with initials dropped ("J. Mem."); fragments dropped when 90% of
  their mentions sit inside a longer term; stoplist matched on singular forms.

Citation parentheticals (parentheses containing a year) are skipped for concepts and
spaCy names: journal abbreviations from source notes ("J. Mem. Lang.") had become
answers ("Mem", "Cogn", "Psychol"). After the fix: Sedivy 496 answers, 38% labeled;
Myers 632, 48% labeled.

Known noise in answers: characters from example stories ("Mr. Biggs") and foreign
example words ("goluboy"); left for the clues stage, which sees whether a term has
real facts.

## clues: first version and golden set (2026-10-08)

- **Coreference: local model** (fastcoref F-coref, MIT, pinned), after a spike on
  chapter 3 (19 s; linked "He considered language..." to Broca). Chosen over rules
  (option B) on Chong's call that ML is the better tool for coreference; CLAUDE.md rule
  1 now allows local pretrained task models. Chains link to an answer only through a
  named mention of that exact answer (leading "the"/"this" allowed).
- **One fact per clue: B with A as fallback.** Split at "; " and between clauses with
  their own subjects; ", which" splits dropped (they produced "Which provided...").
- Candidates fixes found while building the gold set: one-word proper nouns that are a
  person's surname merge into the person; a name inside an established possessive term
  ("Broca" in "Broca's area") is not a mention of the person; in clues, possessives that
  name a thing ("Broca's and Wernicke's areas") are dropped for the person.
- All sentences of an answer's own definition are clues (`via = definition`).

Golden set `eval/clues.tsv`: 20 Sedivy answers (5 people, 15 concepts), 237 sentences,
132 true clues; Claude-labeled, to be reviewed. Scores (`eval/clue_accuracy.py`):

| step | precision | recall | F1 |
|---|---|---|---|
| first version | 79% | 83% | 81% |
| + possessive terms | 82% | 82% | 82% |
| + whole definitions | 82% | 83% | 83% |

Known gap: a person's naming clue ("another of Hockett's features, productivity") is
lost when the name sits inside an established term; one such clue per person could be
kept later. Remaining misses: unlinked pronouns/descriptions (~14 of 22), other word
forms ("bilingual advantage", "gazed").

## candidates: abbreviations; clues gold review (2026-10-09)

- **Abbreviations merged (Schwartz & Hearst 2003)**: pairs the book defines, "long form
  (SHORT)" or "SHORT (long form)", where the short form's letters appear in order in
  the long form starting a word. Plural acronyms join their singular. Before: VOT and
  "voice onset time", ASL and "American Sign Language", ERP and "event-related
  potential" were separate answers. Keyness looks up everyday-English frequency on
  one-word forms only, so a merged long form does not make an acronym look common.
- Chong reviewed the clue gold set for the first 10 answers: 13 labels changed, mostly
  counting a person's own work and studies as clues ("Shortly after Broca's
  discovery", the pointer to Hockett's design features). Rule adopted: a sentence is a
  clue for a person when it says what they proposed, found, made or argued, including
  through something named after them; not when it describes the named thing with
  others' findings. Scores with the reviewed labels: precision 85%, recall 82%.
- Found in review: a bracketed body-size example glued to the next sentences (the open
  decision on body-size indented lines from the tagging review).

## rank and assemble: rough end-to-end (2026-10-09)

Approved bundle (rough first version): **difficulty C** (textbook emphasis + clue
specificity, weights in config); **referent A then B then C** (book's own words, then
the answer's head word, then "this term"/"this researcher"); clues picked hardest first
within the length budget, at most 3 per section, ordered hard to easy; the definition
(or the easiest clue whose subject is the answer) becomes the giveaway; answerline =
display name + merged forms as alternates.

Fixes after reading the first tossups (all in config or code comments):
- referents from how the book names the answer ("X is a gene", "the FOXP2 gene"); a
  person's title only before a full name ("linguist Charles Hockett"), checked with the
  parser; vague heads ("pattern", "region") fall back to the answer's head word;
- the answer must be its own noun phrase: not a modifier ("N400 activity"), not after
  an adjective or "a" ("a bigger N400"), not coordinated ("Broca and Wernicke"), not in
  parentheses, not a value ("a VOT of 0 ms");
- source lines and quote lead-ins (ending in ":") are not clues;
- a clue opening with a pronoun that is not the answer gets the fullest noun-phrase
  mention from its coreference chain ("They found" -> the researchers); "This would
  reduce..." (pointing at an event) is skipped;
- the book's title word is absorbed ("linguist Charles Hockett" -> "this linguist");
  people take "who" in the giveaway.

Sedivy: 219 tossups from 499 answers (330 before the fixes, many unreadable).
Known gaps: answers whose clues mostly contain related terms are lost to the leak check
(garden path sentences: "garden path theory"); FOXP2 and the head-turn preference
paradigm lose clues to the meta-text filter and coreference-only clues that are not
yet masked; coordinated possessives still slip through ("This researcher and
Wernicke's attempts"); 161 answers still use a generic referent.
