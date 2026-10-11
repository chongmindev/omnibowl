"""uv run python -m server [--port 8000] [--room lobby]

Binds 0.0.0.0 so someone on the same Wi-Fi can join, and prints the private links.
"""

import argparse
import socket

import uvicorn

from .main import create_app, server_key


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--room", default="lobby")
    args = ap.parse_args()
    key = server_key()
    app = create_app(key=key)
    print(f"\n  This machine:   http://localhost:{args.port}/{args.room}?k={key}")
    print(f"  Same Wi-Fi:     http://{lan_ip()}:{args.port}/{args.room}?k={key}")
    print("  Elsewhere:      see docs/multiplayer.md (Tailscale or cloudflared)")
    print("  Share the link privately: anyone with the key can read the questions.\n", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
