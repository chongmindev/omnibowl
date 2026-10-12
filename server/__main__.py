"""uv run python -m server [--port 8000] [--room lobby] [--new-key]

Binds 0.0.0.0 so someone on the same Wi-Fi can join, and prints the private links,
including the Tailscale Funnel address when Funnel forwards to this port.
--new-key replaces the key, so every link shared before stops working.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess

import uvicorn

from .main import KEY_FILE, create_app, server_key

TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def lan_ip() -> str:
    """The address other machines on this network reach us at (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def funnel_from_config(config: dict, port: int) -> str | None:
    """The public https address whose "/" proxies to this port with Funnel on, if any.
    `config` is the output of `tailscale serve status --json`."""
    allowed = {k for k, on in (config.get("AllowFunnel") or {}).items() if on}
    for hostport, web in (config.get("Web") or {}).items():
        proxy = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy", "")
        if hostport in allowed and proxy.rstrip("/").endswith(f":{port}"):
            host, _, p = hostport.rpartition(":")
            return f"https://{host}" + ("" if p == "443" else f":{p}")
    return None


def funnel_url(port: int) -> str | None:
    cli = shutil.which("tailscale") or (TAILSCALE_APP if os.path.exists(TAILSCALE_APP) else None)
    if not cli:
        return None
    try:
        out = subprocess.run([cli, "serve", "status", "--json"], capture_output=True, text=True, timeout=5)
        return funnel_from_config(json.loads(out.stdout or "{}"), port)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--room", default="lobby")
    ap.add_argument("--new-key", action="store_true", help="replace the key; old links stop working")
    args = ap.parse_args()
    if args.new_key:
        KEY_FILE.unlink(missing_ok=True)
    key = server_key()
    app = create_app(key=key)
    web = funnel_url(args.port)
    print(f"\n  This machine:   http://localhost:{args.port}/{args.room}?k={key}")
    print(f"  Same Wi-Fi:     http://{lan_ip()}:{args.port}/{args.room}?k={key}")
    if web:
        print(f"  Anywhere:       {web}/{args.room}?k={key}   (Tailscale Funnel)")
    else:
        print(f"  Anywhere:       off. Run `tailscale funnel --bg {args.port}` once (docs/multiplayer.md)")
    print("  Share the link privately: anyone with the key can read the questions.\n", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
