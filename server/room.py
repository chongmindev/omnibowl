"""One multiplayer room: the server owns the tossup, the reveal, buzzes, timers and scores.

Clients only send intents (buzz, answer, next, settings) and render the snapshots the room
broadcasts. The answerline is in a snapshot only once the tossup is over.

States: idle -> loading -> reading -> (buzzed -> reading | window) -> window -> judged,
plus empty (no tossups for the chosen chapters). A wrong buzz locks that player out of
the tossup and the reveal resumes for everyone else, as on protobowl.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .tossups import Library, Tossup

COLORS = ("blue", "yellow", "red", "ink")


@dataclass
class Settings:
    buzz_window: float = 5.0      # seconds to buzz after the last word
    answer_window: float = 8.0    # seconds to answer after a buzz (restarts on a prompt)
    loading_delay: float = 0.35   # pause before the first word of a tossup
    default_wpm: int = 240
    min_wpm: int = 120
    max_wpm: int = 600
    max_name: int = 20
    max_answer: int = 200
    log_length: int = 100         # history entries kept: finished tossups, joins, renames


class Conn(Protocol):
    async def send_json(self, data: Any) -> None: ...


@dataclass
class Player:
    pid: str
    name: str
    color: str
    correct: int = 0
    neg: int = 0
    heard: int = 0
    points: int = 0
    conns: int = 0


@dataclass
class Buzz:
    pid: str
    given: str
    points: int
    word_index: int


@dataclass
class Room:
    name: str
    library: Callable[[], Library]
    settings: Settings = field(default_factory=Settings)
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        lib = self.library()
        self.conns: dict[Conn, str] = {}
        self.players: dict[str, Player] = {}
        self.selected: set[int] = {n for n, _ in lib.chapters}
        self.skip_allowed = False      # skipping a tossup mid-read is a room option, off by default
        self.wpm = self.settings.default_wpm
        self.state = "idle"
        self.empty_reason = ""
        self.tossup: Tossup | None = None
        self.num = 0
        self.i = 0                     # words revealed
        self.buzzer: str | None = None
        self.finished = False          # the current buzz came after the last word
        self.prompted = False
        self.prompt_given = ""         # the answer that drew the prompt, shown and re-judged with the next
        self.typed = ""
        self.locked: set[str] = set()  # players who already buzzed on this tossup
        self.buzzes: list[Buzz] = []
        self.seen: set[str] = set()
        self.log: list[dict] = []
        self.timer: tuple[str, float, float] | None = None  # kind, ends at (monotonic), total
        self.lock = asyncio.Lock()
        self._gen = 0                  # bumped on every transition; stale timer tasks exit
        self._task: asyncio.Task | None = None

    # ---- connection lifecycle -------------------------------------------------------

    async def join(self, conn: Conn, pid: str, name: str) -> None:
        async with self.lock:
            p = self.players.get(pid)
            if p is None:
                p = Player(pid=pid, name=self._clean_name(name) or "guest",
                           color=COLORS[len(self.players) % len(COLORS)])
                self.players[pid] = p
                self._history({"kind": "join", "pid": pid, "name": p.name})
            p.conns += 1
            self.conns[conn] = pid
            if self.state == "idle":
                self._next()
            await self._broadcast()

    async def leave(self, conn: Conn) -> None:
        async with self.lock:
            pid = self.conns.pop(conn, None)
            if pid is None:
                return
            self.players[pid].conns -= 1
            if not self.conns:
                # Nobody left to play: drop the tossup unscored and start fresh on the next join.
                self._transition("idle")
                self.tossup = None
                return
            if self.state in ("reading", "window") and self._all_locked():
                self._end()
            await self._broadcast()

    async def handle(self, conn: Conn, msg: dict) -> None:
        async with self.lock:
            pid = self.conns.get(conn)
            if pid is None:
                return
            kind = msg.get("type")
            if kind == "buzz":
                changed = self._buzz(pid)
            elif kind == "answer":
                changed = self._submit(pid, str(msg.get("text", ""))[: self.settings.max_answer])
            elif kind == "typing":
                if self.state == "buzzed" and pid == self.buzzer:
                    self.typed = str(msg.get("text", ""))[: self.settings.max_answer]
                    await self._send_all({"type": "typing", "pid": pid, "text": self.typed}, skip=conn)
                return
            elif kind == "next":
                changed = self._skip()
            elif kind == "chapters":
                changed = self._set_chapters(msg.get("chapters"))
            elif kind == "skip_allowed":
                self.skip_allowed = bool(msg.get("allowed"))
                changed = True
            elif kind == "wpm":
                changed = self._set_wpm(msg.get("wpm"))
            elif kind == "name":
                changed = self._rename(pid, str(msg.get("name", "")))
            elif kind == "reset_score":
                changed = self._reset_score(pid)
            else:
                changed = False
            if changed:
                await self._broadcast()
            else:
                await self._send(conn, self._snapshot(pid))

    # ---- transitions (called with the lock held) --------------------------------------

    def _transition(self, state: str, timer: tuple[str, float] | None = None) -> int:
        """Enter a state, cancel any running timer task, and return the new generation."""
        self.state = state
        self._gen += 1
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
        self._task = None
        self.timer = (timer[0], time.monotonic() + timer[1], timer[1]) if timer else None
        return self._gen

    def _spawn(self, coro) -> None:
        self._task = asyncio.get_running_loop().create_task(coro)

    def _next(self) -> None:
        lib = self.library()
        chosen = [t for t in lib.tossups if t.chapter in self.selected]
        self.tossup, self.buzzer, self.typed = None, None, ""
        if not self.selected or not chosen:
            self.empty_reason = "no_chapters" if not self.selected else "none_available"
            self._transition("empty")
            return
        pool = [t for t in chosen if t.id not in self.seen]
        if not pool:  # every tossup in these chapters was heard: start the cycle over
            self.seen -= {t.id for t in chosen}
            pool = chosen
        self.tossup = self.rng.choice(pool)
        self.seen.add(self.tossup.id)
        self.num += 1
        self.i = 0
        self.locked, self.buzzes = set(), []
        self.finished = self.prompted = False
        gen = self._transition("loading")
        self._spawn(self._reveal(gen, self.settings.loading_delay))

    def _resume(self) -> None:
        if self.tossup and self.i < len(self.tossup.words):
            gen = self._transition("reading")
            self._spawn(self._reveal(gen, 0))
        else:
            self._start_window()

    def _start_window(self) -> None:
        gen = self._transition("window", ("buzz", self.settings.buzz_window))
        self._spawn(self._expire(gen, self.settings.buzz_window, self._end))

    def _buzz(self, pid: str) -> bool:
        if self.state not in ("reading", "window") or pid in self.locked:
            return False
        self.buzzer, self.typed, self.prompted, self.prompt_given = pid, "", False, ""
        self.finished = self.i >= len(self.tossup.words)
        self.locked.add(pid)
        self._start_answer()
        return True

    def _start_answer(self) -> None:
        gen = self._transition("buzzed", ("answer", self.settings.answer_window))
        self._spawn(self._expire(gen, self.settings.answer_window,
                                 lambda: self._submit(self.buzzer, self.typed)))

    def _submit(self, pid: str | None, text: str) -> bool:
        if self.state != "buzzed" or pid is None or pid != self.buzzer:
            return False
        verdict = self.tossup.judge(text, self.prompted, self.prompt_given)
        if verdict == "prompt":
            self.prompted, self.typed, self.prompt_given = True, "", text.strip()
            self._start_answer()
            return True
        points = 10 if verdict == "accept" else (0 if self.finished else -5)
        given = f"{self.prompt_given} → {text.strip()}" if self.prompted else text.strip()
        self.buzzes.append(Buzz(pid=pid, given=given, points=points, word_index=self.i))
        p = self.players[pid]
        p.points += points
        p.correct += points > 0
        p.neg += points < 0
        self.buzzer, self.typed = None, ""
        if verdict == "accept" or self._all_locked():
            self._end()
        else:
            self._resume()
        return True

    def _end(self) -> None:
        if self.tossup is None:
            return
        for p in self.players.values():
            if p.conns or any(b.pid == p.pid for b in self.buzzes):
                p.heard += 1
        last = self.buzzes[-1] if self.buzzes else None
        # Finished tossups only, so the full question and answer can be reread from the log.
        self._history({"kind": "tossup", "num": self.num, "answer": self.tossup.answer_html,
                            "pid": last.pid if last else None, "points": last.points if last else 0,
                            "question": " ".join(self.tossup.words), "chapter": self.tossup.chapter,
                            "sections": self.tossup.sections, "rewritten": self.tossup.rewritten,
                            "buzzes": [b.__dict__ for b in self.buzzes]})
        self.buzzer = None
        self.i = len(self.tossup.words)
        self._transition("judged")

    def _history(self, entry: dict) -> None:
        self.log.insert(0, entry)
        del self.log[self.settings.log_length:]

    def _skip(self) -> bool:
        if self.state in ("reading", "window") and self.skip_allowed:
            self._end()
        elif self.state in ("judged", "empty"):
            self._next()
        else:
            return False
        return True

    def _all_locked(self) -> bool:
        here = {pid for pid in self.conns.values()}
        return bool(here) and here <= self.locked

    def _set_chapters(self, chapters: Any) -> bool:
        if not isinstance(chapters, list):
            return False
        valid = {n for n, _ in self.library().chapters}
        self.selected = {int(c) for c in chapters if isinstance(c, int) and c in valid}
        if self.state == "empty":
            self._next()
        return True

    def _set_wpm(self, wpm: Any) -> bool:
        if not isinstance(wpm, (int, float)):
            return False
        self.wpm = int(min(self.settings.max_wpm, max(self.settings.min_wpm, wpm)))
        return True

    def _clean_name(self, name: str) -> str:
        return " ".join(name.split())[: self.settings.max_name]

    def _rename(self, pid: str, name: str) -> bool:
        name = self._clean_name(name)
        if not name:
            return False
        old = self.players[pid].name
        if name == old:
            return False
        self.players[pid].name = name
        self._history({"kind": "rename", "pid": pid, "old": old, "name": name})
        return True

    def _reset_score(self, pid: str) -> bool:
        """A player zeroes their own line of the scoreboard; nobody else's changes."""
        p = self.players[pid]
        if not (p.points or p.correct or p.neg or p.heard):
            return False
        p.points = p.correct = p.neg = p.heard = 0
        self._history({"kind": "reset", "pid": pid, "name": p.name})
        return True

    # ---- timer tasks ------------------------------------------------------------------

    async def _reveal(self, gen: int, delay: float) -> None:
        await asyncio.sleep(delay)
        async with self.lock:
            if gen != self._gen:
                return
            if self.state == "loading":
                self.state = "reading"
                await self._broadcast()
        while True:
            await asyncio.sleep(60 / self.wpm)
            async with self.lock:
                if gen != self._gen:
                    return
                self.i += 1
                await self._send_all({"type": "word", "num": self.num, "i": self.i,
                                      "w": self.tossup.words[self.i - 1]})
                if self.i >= len(self.tossup.words):
                    self._start_window()
                    await self._broadcast()
                    return

    async def _expire(self, gen: int, seconds: float, action: Callable[[], Any]) -> None:
        await asyncio.sleep(seconds)
        async with self.lock:
            if gen != self._gen:
                return
            action()
            await self._broadcast()

    # ---- output -----------------------------------------------------------------------

    def _snapshot(self, you: str) -> dict:
        lib = self.library()
        counts: dict[int, int] = {}
        for t in lib.tossups:
            counts[t.chapter] = counts.get(t.chapter, 0) + 1
        players = sorted(self.players.values(), key=lambda p: (-p.points, -p.correct, p.name.lower()))
        snap: dict[str, Any] = {
            "type": "state", "room": self.name, "you": you, "book": lib.book,
            "chapters": [[n, title, counts.get(n, 0)] for n, title in lib.chapters],
            "selected": sorted(self.selected), "wpm": self.wpm,
            # characters in the longest question, so the page can size the question box once
            "max_len": max((len(" ".join(t.words)) for t in lib.tossups), default=0),
            "state": self.state, "empty_reason": self.empty_reason, "num": self.num,
            "buzzer": self.buzzer, "prompted": self.prompted, "prompt_given": self.prompt_given,
            "typed": self.typed,
            "locked": sorted(self.locked), "skip_allowed": self.skip_allowed,
            # Buzzes on the tossup in progress (word_index = words revealed at the buzz), so
            # every screen can mark where each player buzzed. They hold no answerline.
            "buzzes": [b.__dict__ for b in self.buzzes] if self.tossup else [],
            "timer": None, "tossup": None, "result": None,
            "players": [{"pid": p.pid, "name": p.name, "color": p.color, "correct": p.correct,
                         "neg": p.neg, "heard": p.heard, "points": p.points, "away": p.conns == 0}
                        for p in players],
            "log": self.log,
        }
        if self.timer:
            kind, ends, total = self.timer
            snap["timer"] = {"kind": kind, "ms_left": max(0, int((ends - time.monotonic()) * 1000)),
                             "total_ms": int(total * 1000)}
        t = self.tossup
        if t is not None:
            snap["tossup"] = {"chapter": t.chapter, "section": t.section, "words": list(t.words[: self.i])}
            if self.state == "judged":
                snap["result"] = {"answer": t.answer_html, "sections": t.sections, "rewritten": t.rewritten,
                                  "buzzes": [b.__dict__ for b in self.buzzes]}
        return snap

    async def _send(self, conn: Conn, data: dict) -> None:
        try:
            await conn.send_json(data)
        except Exception:  # a closing socket; its handler will call leave()
            pass

    async def _send_all(self, data: dict, skip: Conn | None = None) -> None:
        await asyncio.gather(*(self._send(c, data) for c in list(self.conns) if c is not skip))

    async def _broadcast(self) -> None:
        await asyncio.gather(*(self._send(c, self._snapshot(pid)) for c, pid in list(self.conns.items())))
