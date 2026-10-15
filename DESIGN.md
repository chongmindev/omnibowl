# Omnibowl design

The visual and interaction system for the Omnibowl reader. Code follows this file; if
the design changes, change this file first.

## Direction

Bauhaus, reduced. A black grid of hard-edged cells on warm paper, two typefaces, and
three primary colors that each have one job. Shapes and color carry meaning, never
decoration. The question text is the point of every screen; everything else is small
and quiet around it.

## What we never do

From the patterns that make interfaces look accidental instead of designed:

- No gradients, glow, drop shadows, glassmorphism, sparkles, or emoji in UI text.
- No color used only to look modern. Every color below has a job; a color without a
  job does not appear.
- No rounded corners. Everything is square, so nothing can mismatch. A circle appears
  only as pure geometry (the buzzer in the favicon), never as a softened square.
- No boxed chips, pills or rounded cards. Structure comes from rules (lines) and the grid.
- No cards that lift, grow or bounce on hover. Hover changes color only.
- No oversized heavy headings over thin body text. Weights stay in a narrow range.
- No inconsistent spacing or line height: use the scale below, nothing in between.
- No icons for decoration, and no icon larger than the text beside it.
- No animation without the shared easing curve, and nothing that pops in.
- No control without a visible response: every action shows progress, success or failure.
- No center dots (·) as separators in UI text. Use spacing, commas or " / ".
- No dead controls. A control that isn't built yet is shown disabled and says so on hover.

## Color

| Token | Value | Job |
|---|---|---|
| `--ink` | `#141414` | Text, rules, the black wordmark cell |
| `--paper` | `#F4F2EC` | Page background |
| `--white` | `#FFFFFF` | The current player's row |
| `--mute` | `#5D5A53` | Labels, metadata, hints |
| `--hair` | `#CFCABD` | Hairlines inside a section |
| `--faint` | `#9B978D` | Disabled and inactive |
| `--blue` | `#1D4E9E` | Identity and confirmation: tossup number, textbook stripe, correct (+10), your marker |
| `--yellow` | `#F2B705` | Time: the buzz window and the answer window, the speed slider fill, "next" |
| `--red` | `#D7261E` | The buzzer and penalties: Buzz cell, reading cursor, −5 |

A fourth player color is `--ink`. Text on blue or red is white; text on yellow is ink.
All text meets WCAG AA contrast against its background.

## Type

- **League Spartan** (500, 700): interface, labels, wordmark, numbers in the sidebar.
- **Red Hat Mono** (400, 500): the question text, the answer field, metadata, key hints.

Sizes scale with the viewport so a line holds about the same number of words on any
device:

| Role | Size | Notes |
|---|---|---|
| Question | `clamp(14px, 1.05vw, 22px)` | 400, line height 1.65 |
| Answer field | same as question | 400 |
| Wordmark, tossup number | `clamp(13px, 1vw, 17px)` | 700, uppercase, tracking .06em |
| Body UI | `clamp(12px, .9vw, 15px)` | 500 |
| Labels | `clamp(10px, .75vw, 12px)` | 700, uppercase, tracking .08em |
| Metadata, hints | `clamp(10px, .75vw, 12px)` | Red Hat Mono 400 |
| Score points | `clamp(14px, 1.1vw, 18px)` | 700 |

Only weights 400, 500 and 700 are used. Line height: 1.65 for the question, 1.3 for
everything else.

## Grid and rules

- Outer frame and section dividers: `3px solid var(--ink)`.
- Rows inside a section: `1px solid var(--hair)`.
- Spacing scale (px): 4, 8, 12, 16, 24, 32. Cell padding is 8×16 for compact rows,
  12×16 for header, answer and verdict cells, 12×24 for the action cell, and 24 for the
  question. One derived value: an expanded history row indents 60px (the 48px number
  column plus 12px) so its text lines up with the answer column.
- The layout fills the device edge to edge; there is no maximum width.

## Layout

```
┌──────────┬───────────────────────────────────────────┬────┐
│ OMNIBOWL │ CH. 13 LANGUAGE DIVERSITY / 13.3 ...      │ 04 │  header
├──────────┴───────────────────────────────┬───────────┴────┤
│ (space above the question)               │ TEXTBOOK  Change│
├──────────────────────────────────────────┤ Language in Mind│
│ question text, full width of the column  ├─────────────────┤
│ time bar (yellow, when running)          │ SCOREBOARD      │
│                                          │ # player +10 ...│
├──────────────────────────────────────────┤─────────────────┤
│ answer row (when buzzed)                 │ SPEED  ━━━━┃──  │
│ verdict row (when judged)                ├─────────────────┤
├──────────────────────────────┬───────────┤ CHAPTERS IN PLAY│
│ Space to buzz                │   BUZZ    │ 6 of 13 All None│
├──────────────────────────────┴───────────┤ ☑ 03 ...        │
│ log: 03 Broca's area              +10    │ ☐ 07 ...        │
│      Mina joined the game                │                 │
└──────────────────────────────────────────┴─────────────────┘
```

- Main column left, sidebar right (fixed width `clamp(240px, 20vw, 340px)`).
- The page never scrolls: it fills the window and the history scrolls inside its own
  area. In rooms the sidebar scrolls as a whole; in single player only the chapter list
  scrolls. The action bar (Buzz / Next) sits right under the question, answer and
  verdict rows, so the hand and the eye stay in one place.
- Below 900px wide, the sidebar moves under the reader as a single column; there the
  page scrolls and the action bar sticks to the bottom of the screen.

## Components

- **Header.** Black wordmark cell, a metadata cell, and a blue tossup-number cell. For a
  generated tossup the metadata reads "Generated tossup" and never names the chapter or
  section, which would be a clue (they appear in the verdict and history after the
  tossup); for a QBReader stand-in it
  is "QBREADER / <set name> <year> / filed under CH. N", so it never reads as if the
  textbook wrote it.
- **Question.** Red Hat Mono, full column width. A 2px red cursor follows the last
  revealed word. The question block does not start at the top of the column: it sits
  `clamp(28px, 12vh, 140px)` down, with a 3px rule above it, so the eye lands on it
  lower on the screen.
- **Buzz markers.** Each buzz is marked in the question text at the word where it
  happened: the player's 9px square and their name in uppercase label type, colored by
  the result (blue correct, red wrong, mute 0); "buzzing" in red while they answer. The
  color carries the result, so no points are written. No box around it. Markers stay
  when the question is reread from the history. Rooms and single player alike.
- **Stable layout.** The question box is sized for the book's longest tossup (plus one
  line for buzz markers), and the answer and verdict rows share one slot that keeps the
  answer row's height, so the action button never moves between reading, buzzing,
  judging and the next tossup. A closed answer box gives up focus, so Space and → work
  right after a verdict.
- **Answer row.** Opens under the question on buzz: an ink label cell "Answer" and a
  text field. A prompt shows "More specific than “<what you said>”?" in the label cell. In
  rooms, other players see the row open read-only with "<name> answering" (or "<name>
  prompted on “…”") in the label cell
  and the buzzer's typing as it happens.
- **Verdict row.** A colored result cell (blue "+10 correct", red "−5 incorrect",
  red "0 incorrect", red "0 no buzz"; another player's name follows when the deciding
  buzz was theirs), everyone's answers in mute, the answerline with the required part underlined, and the
  source: chapters and sections for generated tossups, or set name and year for
  QBReader tossups, marked "QBReader".
- **History.** Newest first, hairline rows, the last 100 tossups (rooms and single
  player). A finished tossup shows number, answer, and
  result colored by sign (with the player's name if not you); clicking it (or Enter)
  expands it in place to the full question with buzz markers, the answerline, who said
  what, and its chapter and sections. Room events ("Mina joined the game", "guest4821
  renamed to Mina") sit between them as single mono `--mute` lines.
- **Time bar.** 10px yellow bar attached directly under the question (a hairline
  between them), draining left to right, so the clock sits next to what you are
  reading. Used for the buzz window after the question ends and for the answer window
  after a buzz. Nothing tracks progress while the question is being read.
- **Action bar.** Directly under the question, answer and verdict rows, above the
  history. Key hints in mono on the left ("Space to buzz", "Enter to submit"); the
  action cell on the right: red "Buzz", red "Submit" while answering, disabled "Locked
  out" after your wrong buzz, yellow "Next →" after a verdict.
- **Sidebar sections.** Each has an uppercase label row over its content.
  - Textbook: title, author, edition, year, with a blue stripe; a disabled "Change"
    control whose hover note reads "Not built yet: one textbook for now".
  - Scoreboard: a ranked player table (rank, player, +10, −5, heard, points). Solo play
    is a one-row table. Your row is white with a 4px blue left edge. In rooms, a
    4px red edge and "buzzing" mark whoever holds the buzz, and away players use
    `--faint`. Each player has a 9px square marker in blue, yellow, red or ink.
    The label row ends in a "Score Reset" text button (mono, like All/None) that
    zeroes your own row only. It takes two clicks: the first turns it red and reads
    "Confirm reset" for 3 seconds. It is disabled while your row is all zeros.
    Label rows never wrap: the count and the button sit on one line.
  - Speed: a slider; the track is hairline-outlined, the fill yellow, the handle a 3px
    ink bar. Shows words per minute.
  - Room: square checkboxes for room-wide options, currently "Allow skipping
    mid-question" (off by default).
  - Chapters in play: the label row carries the count ("6 of 13"). Under it, a help
    row: one mono `--mute` line, "Only checked chapters are asked.", with
    two plain controls at its right, All and None, each `--faint` and inert when it
    would change nothing. Then
    a list of square checkboxes (11px, 2px ink border; filled ink with a white check
    when on; hover shades an empty box), chapter number in mono, title, and the number
    of tossups. Unchecked chapters use `--faint`.
- **Your name.** You start as `guest` plus four random digits (e.g. `guest4821`). On the
  first visit only, a black note under your scoreboard row reads "You're guest4821.
  Click to rename." Clicking your row turns your name into a text field in place (same
  type, a 2px ink underline); Enter or clicking elsewhere saves, Esc cancels. Names are 1 to
  20 characters, trimmed; empty reverts to the previous name. Saved on the device.
- **Favicon.** A buzzer seen from the side: a red half-disc on an ink base, on paper.
  Inline SVG, no file.
- **Disabled control.** `--faint` text, `not-allowed` cursor, a black hover note in mono.

## Motion

- One easing curve, `cubic-bezier(.2, .7, .2, 1)`. Durations: 120 ms for color, 150 ms
  for rows opening, 200 ms for the verdict.
- Hover changes background or text color only.
- Word reveal is instant per word at the chosen speed; no fades on words.
- The time bar drains linearly (it is a clock, so it does not ease).
- `prefers-reduced-motion`: rows appear without sliding; the time bar still drains.

## States

| State | What the reader shows |
|---|---|
| Loading a tossup | The question area holds only the blinking red cursor. |
| Reading | Words appear at the chosen speed; red cursor after the last word. |
| Buzz window | The question is complete; the yellow bar drains; Space still buzzes. |
| Buzzed | Reading stops at once; the answer row opens with focus in the field; the yellow bar drains the answer window. |
| Checking | The submit cell reads "Checking" with a short sweep and is disabled until the result returns. |
| Prompt | The answer row stays open with "Be more specific"; the answer window restarts. |
| Judged | Verdict row appears; full question shown; the tossup is added to the log; the action cell turns yellow "Next →". |
| Answer check offline | Falls back to a local match and marks the verdict "checked offline". |
| Time runs out | Buzz window: the tossup is judged as unanswered (0). Answer window: whatever is typed is submitted. |
| No tossups | The question area explains why: no chapters selected, or none available for them. |
| Server unreachable | The question area says so and offers a Retry action. |

## Keyboard

| Key | Action |
|---|---|
| Space | Buzz (while reading or in the buzz window) |
| Enter | Submit the answer |
| → | Next tossup; skip while reading only when the room allows skipping |

Speed is changed only with the slider. Every action also works by pointer and touch, and
every control has a visible focus state: a 2px ink outline offset 2px.
- **Single player remembers progress.** Score, history (up to 100) and the tossups already
  asked are saved in the browser after every finished tossup, so a reload or a new visit
  picks up where you left off (same browser and device only). Score Reset clears the score
  and keeps the history; "Reset first visit" clears everything.
