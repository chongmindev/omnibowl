"""End to end over a real socket: private key, room page, WebSocket join. Fake tossups only."""

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import uvicorn
import websockets

from server.main import create_app

KEY = "testkey"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_key_page_and_websocket(tmp_path):
    path = tmp_path / "tossups.json"
    path.write_text(json.dumps({"book": "Fake", "chapters": [[1, "One"]], "tossups": [
        {"id": "x", "ch": 1, "sec": "1.1", "secs": "1.1", "q": "alpha beta gamma",
         "a": "<b><u>Secret</u></b>", "acc": ["secret"], "prompt": [], "rw": True}]}))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(path, key=KEY), port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    try:
        try:
            urllib.request.urlopen(base + "/lobby")
            raise AssertionError("page served without the key")
        except urllib.error.HTTPError as e:
            assert e.code == 403

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None
        try:
            urllib.request.build_opener(NoRedirect).open(base + f"/Lobby?k={KEY}")
        except urllib.error.HTTPError as e:
            assert e.code == 307 and e.headers["location"] == "/lobby"
            cookie = e.headers["set-cookie"].split(";")[0]
        req = urllib.request.Request(base + "/lobby", headers={"Cookie": cookie})
        assert "Omnibowl room" in urllib.request.urlopen(req).read().decode()

        async def play():
            try:
                async with websockets.connect(f"ws://127.0.0.1:{port}/ws/lobby") as ws:
                    await ws.recv()
                raise AssertionError("socket opened without the key")
            except websockets.InvalidStatus:
                pass
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/lobby",
                                          additional_headers={"Cookie": cookie}) as ws:
                await ws.send(json.dumps({"type": "hello", "pid": "p1", "name": "alice"}))
                seen = []
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    seen.append(m)
                    if m["type"] == "state" and m["state"] == "window":
                        break
                assert "Secret" not in json.dumps(seen)
                await ws.send(json.dumps({"type": "buzz"}))
                await ws.send(json.dumps({"type": "answer", "text": "secret"}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    if m["type"] == "state" and m["state"] == "judged":
                        break
                assert m["players"][0]["points"] == 10 and "Secret" in m["result"]["answer"]
        asyncio.run(play())
    finally:
        server.should_exit = True
