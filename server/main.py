"""Omnibowl room server: one WebSocket room per path, as on protobowl.

    uv run python -m server            # binds 0.0.0.0:8000 and prints the share links
    uv run uvicorn server.main:create_app --factory --reload   # local development

GET /<room> serves the player page; the page opens WS /ws/<room>. Every request needs the
private key (questions come from a copyrighted book): a link carrying ?k=<key> sets a
cookie, and requests without a valid cookie get a 403. The key comes from OMNIBOWL_KEY,
or is created once in data/server_key (gitignored).
"""

import os
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse

from .room import Room
from .tossups import DEFAULT_PATH, ROOT, Library, load

PAGE = ROOT / "web" / "index.html"
KEY_FILE = ROOT / "data" / "server_key"
COOKIE = "ob_key"
ROOM_NAME = re.compile(r"[a-z0-9_-]{1,40}")


def server_key() -> str:
    if os.environ.get("OMNIBOWL_KEY"):
        return os.environ["OMNIBOWL_KEY"]
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(secrets.token_urlsafe(12))
    return KEY_FILE.read_text().strip()


class Tossups:
    """Reloads data/play/tossups.json when tools/play.py rewrites it."""

    def __init__(self, path: Path) -> None:
        self.path, self.mtime, self.lib = path, 0.0, None

    def __call__(self) -> Library:
        mtime = self.path.stat().st_mtime
        if self.lib is None or mtime != self.mtime:
            self.lib, self.mtime = load(self.path), mtime
        return self.lib


def create_app(tossups_path: Path = DEFAULT_PATH, key: str | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    library = Tossups(Path(os.environ.get("OMNIBOWL_TOSSUPS", tossups_path)))
    library()  # fail at startup, not on the first join, if the file is missing
    key = key or server_key()
    rooms: dict[str, Room] = {}

    def denied() -> HTMLResponse:
        return HTMLResponse("<p style='font-family:monospace'>Private room. Ask the host for the link.</p>",
                            status_code=403)

    @app.get("/")
    def root(request: Request):
        return RedirectResponse("/lobby" + (f"?{request.url.query}" if request.url.query else ""))

    @app.get("/{room}")
    def page(room: str, request: Request, k: str | None = None):
        name = room.lower()
        if not ROOM_NAME.fullmatch(name):
            return HTMLResponse("Room names use letters, digits, - and _.", status_code=404)
        if k is not None:
            if not secrets.compare_digest(k, key):
                return denied()
            resp = RedirectResponse(f"/{name}")  # drop the key from the address bar
            resp.set_cookie(COOKIE, key, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax")
            return resp
        if not secrets.compare_digest(request.cookies.get(COOKIE, ""), key):
            return denied()
        if name != room:
            return RedirectResponse(f"/{name}")
        return HTMLResponse(PAGE.read_text(), headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/{room}")
    async def ws(websocket: WebSocket, room: str):
        name = room.lower()
        if not ROOM_NAME.fullmatch(name) or not secrets.compare_digest(websocket.cookies.get(COOKIE, ""), key):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            hello = await websocket.receive_json()
        except (WebSocketDisconnect, ValueError):
            return
        if not isinstance(hello, dict):
            await websocket.close(code=1003)
            return
        pid = str(hello.get("pid", ""))[:40] or secrets.token_hex(8)
        r = rooms.get(name)
        if r is None:
            r = rooms[name] = Room(name=name, library=library)
        await r.join(websocket, pid, str(hello.get("name", "")))
        try:
            while True:
                msg = await websocket.receive_json()
                if isinstance(msg, dict):
                    await r.handle(websocket, msg)
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            await r.leave(websocket)

    return app

