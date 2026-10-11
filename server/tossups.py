"""Tossups for the room server, read from data/play/tossups.json (written by tools/play.py).

The server never reads the database, so a pipeline rerun cannot break a game in progress.
Answer judging is the same local match the prototype player uses: the normalized answer
must equal one of the tossup's accepted forms; a prompt form asks once for more.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "play" / "tossups.json"


def norm(s: str) -> str:
    """Same normalization as tools/play.py and the prototype player."""
    s = re.sub(r"<[^>]+>", "", s).lower().replace("’", "").replace("'", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


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

    def judge(self, given: str, prompted: bool) -> str:
        """'accept', 'prompt' or 'reject'. A second prompt in a row is a reject."""
        g = norm(given)
        if g and g in self.accept:
            return "accept"
        if g and not prompted and g in self.prompt:
            return "prompt"
        return "reject"


@dataclass(frozen=True)
class Library:
    book: str
    chapters: tuple[tuple[int, str], ...]
    tossups: tuple[Tossup, ...]


def load(path: Path = DEFAULT_PATH) -> Library:
    data = json.loads(Path(path).read_text())
    tossups = tuple(
        Tossup(id=t["id"], chapter=int(t["ch"]), section=t.get("sec", ""), sections=t.get("secs", ""),
               words=tuple(t["q"].split()), answer_html=t["a"],
               accept=frozenset(norm(a) for a in t["acc"] if norm(a)),
               prompt=frozenset(norm(p) for p in t.get("prompt", []) if norm(p)),
               rewritten=bool(t.get("rw")))
        for t in data["tossups"] if t["q"].strip())
    chapters = tuple((int(n), title) for n, title in data["chapters"])
    return Library(book=data.get("book", ""), chapters=chapters, tossups=tossups)
