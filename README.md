# Omnibowl

Turn a textbook into quizbowl tossups, so a class can be studied in the style of
[protobowl](https://protobowl.com) and [QBReader](https://www.qbreader.org).

A build pipeline reads a textbook PDF and writes pyramidal tossups to a database. A
reader app reveals them word by word and lets you buzz. Every clue points back to the
textbook sentence it came from, so nothing appears in a question that the book does not
say.

Generation uses no LLM and no hosted model. It is classical NLP (part-of-speech
patterns, corpus statistics, rules) plus one local pretrained model for coreference,
pinned and run on your machine. Rules and thresholds are generic; each book's layout is
discovered into a profile rather than hard-coded.

## Status

| Stage | Produces | Status |
|---|---|---|
| `parse` | sections, lines, tagged blocks, sentences, glossary and index labels | done |
| `candidates` | answer terms with keyness, coverage and a class | done |
| `coref` | coreference chains | done |
| `clues` | one-fact clues per answer, grounded in sentence ids | first version; being tuned |
| `rank` | referent, giveaway, clue difficulty | not started |
| `assemble` | tossups, ordered hard to easy | not started |
| reader | web app that reveals tossups and records buzzes | designed (`DESIGN.md`, spec in `docs/superpowers/specs/`); not built |

Tested on two textbooks with different layouts: a single-column psycholinguistics text
and a converted AP psychology ebook (three-level outline, panels, two-column glossary).

## Pipeline

```
PDF -> measure -> profile -> tag -> sentences -> back matter      (parse)
    -> candidates -> coref -> clues -> rank -> assemble           (tossups)
```

Each stage reads the previous stage's stored output from SQLite and writes its own
tables, so any stage can be rerun alone.

### parse

1. **Measure** (`parse.py`, PyMuPDF). Reads the PDF outline into chapters and
   sections and records every text line with size, weight, position, background panel
   and internal links. Outline roles come from structure: a top-level entry is content
   if it or a descendant is numbered ("3", "3.1", "Unit IV", "Module 12"). Outline
   destinations that point to a page top are moved to the matching heading line.
   Two-column pages are read column by column. Font sizes are stored relative to each
   chapter's body size, since converted ebooks scale chapters differently.
2. **Profile** (`profile.py`). Discovers the book's layout and writes
   `data/profiles/<book>.auto.toml`: font-size bands from the size histogram's peaks;
   recurring feature markers (short bold lines seen across chapters, numbers
   normalized away) with a guessed role (box, exercise, objective, summary, table,
   heading) from an English word list and from what follows them; panel styles grouped
   by fill color with a guessed role (caption, table, objective, definition, box); page
   furniture; and back-matter parts (glossary, subject index, author index) identified
   by their contents. Hand corrections go in `profiles/<book>.toml` (committed).
3. **Tag** (`tag.py`). Labels every line (body, box, caption, definition, exercise,
   objective, summary, table, example, heading, drop cap, furniture) with an ordered
   rule list driven only by the profile. A marker opens a region that ends with its
   panel, at the next heading or marker, or when side-size text returns to body size.
   Lines that name a region (markers, titles, short all-bold lines in boxes) are
   headings that keep the region's name.
4. **Sentences** (`sentences.py`, spaCy `en_core_web_sm`). Stitches each stream of
   text back together (body prose continues across interrupting sidebars; drop caps
   rejoin their word; a line-final hyphen is kept or removed by comparing the book's own
   use of the joined and hyphenated forms) and splits it with spaCy's dependency parser.
   Two corrections: never end a sentence inside open parentheses, and drop a caption's
   bold run-in label from its first sentence. Each sentence keeps its line ids and
   character offsets.
5. **Back matter** (`backmatter.py`). Reads the glossary (bold term, definition,
   introduction page) and indexes (entries, sub-entries, linked pages) into `labels`.
   Labels are only used to evaluate later stages, never as their input.

### candidates (`candidates.py`)

- **Extraction.** Concepts from part-of-speech patterns over spaCy tags, after
  Justeson & Katz (1995): adjectives and nouns ending in a noun, optional "noun of
  noun", hyphenated compounds and possessives inside, up to five words; single
  technical nouns included. People from spaCy named entities plus surnames in citations
  ("Saffran et al., 1996"). Text inside citation parentheticals is skipped.
- **Variants merged**: same words in any order or form; synonym head words from config
  ("color terms" = "color words"); abbreviations the book defines, found with the
  Schwartz & Hearst (2003) algorithm ("VOT" = "voice onset time"); a surname that is
  also a one-word noun joins the person. Fragments inside longer terms and phrases
  starting with determiner-like adjectives ("other languages") are dropped.
- **Keyness.** One-word terms and people: smoothed log ratio of frequency in the book
  against everyday English from `wordfreq` (Kilgarriff's "simple maths"). Multi-word
  terms, which `wordfreq` cannot estimate: local mutual information inside the book
  (mentions x PMI, Evert 2005).
- **Coverage**: distinct sentences mentioning the term, and how many chapters do.
- **Class**: `too_broad` (in 10+ of 13 chapters), `answer` (5+ sentences and keyness
  above the answer line), `clue_only`, or `not_term`. Lines are hand-set in
  `config.toml`.

### coref (`coref.py`)

Runs fastcoref's F-coref model (MIT license, pinned revision, downloaded once to the
Hugging Face cache, runs on Apple's GPU when available) over each text unit and stores
the chains. About two minutes per book.

### clues (`clues.py`)

For each answer: sentences that name it, plus sentences reached through a coreference
chain that contains a named mention of exactly that answer ("He considered language
to be a good test case..." for Broca), plus every sentence of the answer's own
definition. Sentences are split into one-fact clauses at semicolons and between clauses
with their own subjects; otherwise the whole sentence is kept. Possessives that name a
thing ("Broca's area") are not mentions of the person. Each clue records its sentence
ids, section, kind of text, and how it was found (named, coref, definition).

## Libraries

| Library | Used for |
|---|---|
| [PyMuPDF](https://pymupdf.readthedocs.io) | reading the PDF: text spans, outline, drawings, links |
| [spaCy](https://spacy.io) + `en_core_web_sm` | sentence boundaries, part-of-speech tags, dependency parses, named entities |
| [wordfreq](https://github.com/rspeer/wordfreq) | everyday-English word frequencies for keyness |
| [fastcoref](https://github.com/shon-otmazgin/fastcoref) (`transformers` < 5, PyTorch) | coreference chains (F-coref model) |
| SQLite (standard library) | all pipeline data |
| pytest | tests |

Planned for the reader: FastAPI and uvicorn (server), Vite and TypeScript (web), and
QBReader's public API for answer checking.

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync
```

The first `coref` run downloads the F-coref model (a few hundred MB) to
`~/.cache/huggingface`. Textbooks are not included: put a text-layer PDF (no OCR) in
`data/books/`.

## Usage

```
uv run python -m omnibowl.run --stage parse --book data/books/<file>.pdf        # measure, profile, tag, sentences, back matter
uv run python -m omnibowl.run --stage candidates --book data/books/<file>.pdf
uv run python -m omnibowl.run --stage coref --book data/books/<file>.pdf
uv run python -m omnibowl.run --stage clues --book data/books/<file>.pdf
uv run pytest
```

`--stage profile`, `tag`, `sentences` and `backmatter` rerun parse from that step on.
Output goes to `data/omnibowl.db`.

## Evaluation

All eval files refer to text by position (page, line, character offset), never by
quoted text.

| Script | Measures | Current (v0 book) |
|---|---|---|
| `eval/tag_accuracy.py` | line tagging vs. 150 hand-labeled lines (two books) | 97% |
| `eval/sentence_accuracy.py` | sentence splits vs. 50 judged sentences | 98% |
| `eval/candidate_recall.py` | glossary and index terms found among candidates | glossary 82% |
| `eval/candidate_ranking.py` | precision at k of keyness rankings vs. labels | see `docs/decisions.md` |
| `eval/candidate_classes.py` | labeled share per class | answers 41% labeled |
| `eval/clue_accuracy.py` | clues vs. a 20-answer golden set | precision 85%, recall 82% |

`eval/review_tags.py` and `eval/review_clues.py` build local review pages (written to
`data/review/`, since they contain book text) for checking labels by eye.

## Layout

```
pipeline/omnibowl/   pipeline stages and CLI (run.py)
pipeline/config.toml all thresholds and weights; no magic numbers in code
pipeline/tests/      pytest suite
profiles/            hand corrections to generated layout profiles
eval/                labeled data and metric scripts
docs/                design notes, decisions.md, reader spec
DESIGN.md            visual and interaction system for the reader
data/                books, database, profiles, caches (gitignored)
```

Every algorithm choice, with the options rejected and the measurements behind it, is
in `docs/decisions.md`.

## Content policy

Textbook content never enters the repository: PDFs, extracted text, the database and
review pages stay in `data/`. Questions generated from a copyrighted textbook are for
personal study.
