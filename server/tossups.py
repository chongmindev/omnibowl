"""Tossups for the room server, read from data/play/tossups.json (written by tools/play.py).

The server never reads the database, so a pipeline rerun cannot break a game in progress.
Answer judging is server/judge.py (same rules as the single-player page): accepted forms,
typos within limits, and prompt forms that ask once for more; the rules come from the
file's "judge" entry.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .judge import Rules, judge, norm

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "play" / "tossups.json"



@dataclass(frozen=True)
class Tossup:
    id: str
    chapter: int
    section: str
    sections: str
    words: tuple[str, ...]
    answer_html: str
    accept: frozenset[str]
    prompt: frozenset[str]
    rewritten: bool
    rules: Rules = Rules()

    def judge(self, given: str, prompted: bool) -> str:
        """'accept', 'prompt' or 'reject'. A second prompt in a row is a reject."""
        return judge(given, self.accept, self.prompt, self.rules, prompted)


@dataclass(frozen=True)
class Library:
    book: str
    chapters: tuple[tuple[int, str], ...]
    tossups: tuple[Tossup, ...]


def load(path: Path = DEFAULT_PATH) -> Library:
    data = json.loads(Path(path).read_text())
    rules = Rules.from_json(data.get("judge", {}))
    tossups = tuple(
        Tossup(id=t["id"], chapter=int(t["ch"]), section=t.get("sec", ""), sections=t.get("secs", ""),
               words=tuple(t["q"].split()), answer_html=t["a"],
               accept=frozenset(norm(a) for a in t["acc"] if norm(a)),
               prompt=frozenset(norm(p) for p in t.get("prompt", []) if norm(p)),
               rewritten=bool(t.get("rw")), rules=rules)
        for t in data["tossups"] if t["q"].strip())
    chapters = tuple((int(n), title) for n, title in data["chapters"])
    return Library(book=data.get("book", ""), chapters=chapters, tossups=tossups)
