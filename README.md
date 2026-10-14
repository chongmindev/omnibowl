# Omnibowl

Turn a textbook into quizbowl tossups, so a class can be studied in the style of
[protobowl](https://protobowl.com) and [QBReader](https://www.qbreader.org).

A build pipeline reads a textbook PDF and writes pyramidal tossups to a database. A
player reveals them word by word and lets you buzz, alone or in a protobowl-style room
with friends. Every clue points back to the
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
| `qbreader` | how often real quizbowl tossups use each book term, and where | done (cached lookups) |
| `rank` | referent, giveaway, clue difficulty features, clue uniqueness | working; difficulty weights being tuned |
| `assemble` | tossups, ordered hard to easy | rough first version (455 drafts for the v0 book) |
| single-player page | one self-contained HTML file: reveal, buzz, judging, score, history | working |
| multiplayer rooms | protobowl-style rooms on a FastAPI WebSocket server | v0 working (private link, reachable from anywhere through Tailscale Funnel) |

For the v0 book, drafts are rewritten into quizbowl wording in Claude Code sessions and
checked against their source sentences by a classical verifier (`docs/rewrite-v1.md`);
the pipeline still chooses every answer and fact. 266 verified tossups are in the player.
The verifier also checks that each lead-in's "this ..." handle fits at least three answers
in the played pool, so the first words never give the answer away.

**Now working on:** clue difficulty. Played tossups did not always feel pyramidal, so
`rank` now stores four signals per clue (placement in the book, how often the book
restates the fact, how detailed it is, and how familiar its terms are in real quizbowl).
Their weights are being tuned against hand-ordered tossups (`eval/difficulty_gold.json`);
until then the original score stays live.

Tested on two textbooks with different layouts: a single-column psycholinguistics text
and a converted AP psychology ebook (three-level outline, panels, two-column glossary).

## Pipeline

```
PDF -> measure -> profile -> tag -> sentences -> back matter      (parse)
    -> candidates -> coref -> clues -> qbreader -> rank -> assemble   (tossups)
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
  also a one-word noun joins the person. An answer that is another answer of the same
  kind plus light trailing nouns ("N400 effect") becomes a variant of the shorter one
  and plays as it. Fragments inside longer terms and phrases
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
chain that contains a named mention of exactly that answer (a sentence starting "He
argued..." right after one naming the researcher), plus every sentence of the answer's own
definition. Sentences are split into one-fact clauses at semicolons and between clauses
with their own subjects; otherwise the whole sentence is kept. Possessives that name a
thing ("Broca's area") are not mentions of the person. Each clue records its sentence
ids, section, kind of text, and how it was found (named, coref, definition).

### qbreader (`qbreader.py`)

For every book term that appears inside some clue, searches QBReader's tossup text for
the exact phrase and stores how many real tossups use it and how far into the question it
usually comes. Every response is cached in `data/qbreader_cache/`, so the roughly 40
minutes of lookups happen once per book; later runs read the cache. A book whose terms
quizbowl never uses simply gets no familiarity signal.

### rank (`rank.py`, `difficulty.py`)

Clue difficulty combines textbook emphasis (definitions, summaries and bold introductions
are easy; boxes, captions, coreference-only and off-chapter clues are harder) with
specificity (how rare the clue's most distinctive words are in the book). Four more
signals are stored per clue for tuning: repetition (a fact the book restates is easier),
detail (years, numbers, citations, people and quoted stimuli make a clue harder) and
QBReader familiarity (terms real tossups use often, and late, are easier). Weights live
in `config.toml`. Referent: the
kind of thing the book calls the answer ("X is a gene", "linguist Charles Hockett"),
else the head noun of its definition, else its own head word, else "this term". The
definition, after its head noun, becomes the giveaway.

Clue uniqueness (`uniqueness.py`) is a retrieval test: each answer's clue sentences form
a document, each clue minus the answer's words is a query, and every answer is ranked
against it by query likelihood. A clue is unique when its own answer ranks first. Stored
per clue as rank and margin; not yet used to pick clues.

### assemble (`assemble.py`)

Replaces the answer with the referent ("this effect"), dangling pronouns with the name
from their coreference chain, and skips clues where the answer is a modifier, a value,
coordinated with another name, or still leaks. Picks clues hardest first within 4 to 7
sentences and 500 to 700 characters, ends with "For 10 points, name this ...", and
stores tossups in QBReader's format with source, sections and clue ids.

## Libraries

| Library | Used for |
|---|---|
| [PyMuPDF](https://pymupdf.readthedocs.io) | reading the PDF: text spans, outline, drawings, links |
| [spaCy](https://spacy.io) + `en_core_web_sm` | sentence boundaries, part-of-speech tags, dependency parses, named entities |
| [wordfreq](https://github.com/rspeer/wordfreq) | everyday-English word frequencies for keyness |
| [fastcoref](https://github.com/shon-otmazgin/fastcoref) (`transformers` < 5, PyTorch) | coreference chains (F-coref model) |
| SQLite (standard library) | all pipeline data |
| [FastAPI](https://fastapi.tiangolo.com) + [uvicorn](https://www.uvicorn.org) | multiplayer room server (HTTP and WebSockets) |
| pytest | tests |

The player pages are plain HTML and JavaScript with no build step. Answers are judged
locally, with the same rules in `server/judge.py` and `tools/judge.js`: the answerline or
an alternate, ignoring case, punctuation, articles and word order; small typos and
inflections ("testing" for "test") unless the guess is a different book term; a person's
surname; an abbreviation or its expansion; and one prompt on a partial answer ("aphasia"
for "Broca's aphasia"), where the next answer is tried together with the first. The
answerline underlines only what is required ("<u>test</u> phase", "Paul <u>Broca</u>").
QBReader's API is used only by the `qbreader` stage.

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
uv run python -m omnibowl.run --stage qbreader --book data/books/<file>.pdf     # network the first time, then cached
uv run python -m omnibowl.run --stage rank --book data/books/<file>.pdf
uv run python -m omnibowl.run --stage assemble --book data/books/<file>.pdf
uv run pytest
```

Play the generated tossups (both write to and read from `data/`):

```
uv run python tools/play.py && open data/play/index.html    # single player
uv run python -m server                                      # multiplayer: prints private links
```

The server binds port 8000 on all interfaces and prints a link for this machine and one
for the same Wi-Fi, each with a private key, plus an "Anywhere" link when Tailscale Funnel
is on. Any path is a room (`/lobby`, `/psych101`). Rooms have a scoreboard (each player
can reset their own score), shared reading speed and chapter filter, and a history of
finished tossups that can be reread. The server runs on your machine, so rooms are
reachable only while it is awake and running; the single-player file works anywhere,
offline. Setup and room rules are in `docs/multiplayer.md`.

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
| `eval/clue_uniqueness.py` | whether clue uniqueness predicts answers the rewrite skipped | AUC 0.55 overall; see `docs/decisions.md` |
| `eval/pyramid.py` | clue order of played tossups under each difficulty score, vs. hand orders | 89% of sentences linked to a source; 0.75 pair agreement with 5 gold orders |

`eval/review_tags.py`, `eval/review_clues.py` and `eval/review_difficulty.py` build local review pages (written to
`data/review/`, since they contain book text) for checking labels by eye.

## Layout

```
pipeline/omnibowl/   pipeline stages and CLI (run.py)
pipeline/config.toml all thresholds and weights; no magic numbers in code
pipeline/tests/      pytest suite
server/              multiplayer room server (FastAPI), answer judge, tests
web/                 room page (plain HTML/JS)
tools/               player export (play.py), v1 rewrite batches, verifier, lead-in handle fixes
profiles/            hand corrections to generated layout profiles
eval/                labeled data and metric scripts
docs/                design notes, decisions.md, reader spec, multiplayer.md
DESIGN.md            visual and interaction system for the reader
data/                books, database, profiles, caches (gitignored)
```

Every algorithm choice, with the options rejected and the measurements behind it, is
in `docs/decisions.md`. A proposed optional LLM layer (grounded rewording with
verification; not built) is described in `docs/llm-layer-plan.md`.

## Content policy

Textbook content never enters the repository: PDFs, extracted text, the database,
rewrite batches and review pages stay in `data/`. The one exception is the v0 book's
single-player page (`play/index.html`), which may be published, credited to the book, for
personal study (decision 2026-10-13). Use a textbook you are licensed to use. Generated
questions closely paraphrase the book, and credit is not permission; share them only as
far as the book's license allows (openly licensed books, such as OpenStax titles under CC
BY, can be shared with attribution).

## License

Omnibowl is licensed under the GNU Affero General Public License v3.0 (`LICENSE`).
It depends on PyMuPDF, which is itself AGPL-3.0; the other dependencies are permissive
(spaCy, en_core_web_sm, fastcoref and the F-coref model: MIT; transformers, wordfreq
code: Apache-2.0; PyTorch: BSD; wordfreq's frequency data: CC BY-SA 4.0, installed by
users, not redistributed here).

`server/blocked_words.txt`, the list used to refuse offensive player names, is the English
"List of Dirty, Naughty, Obscene, and Otherwise Bad Words" (LDNOOBW, Shutterstock),
CC BY 4.0, included unchanged except for a header.
