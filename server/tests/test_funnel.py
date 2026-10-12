"""The Funnel address printed at startup comes from `tailscale serve status --json`."""

from server.__main__ import funnel_from_config

HOST = "mac.example.ts.net"


def config(port=8000, funnel=True, listen=443):
    hp = f"{HOST}:{listen}"
    return {"TCP": {str(listen): {"HTTPS": True}},
            "Web": {hp: {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}},
            "AllowFunnel": {hp: True} if funnel else {}}


def test_funnel_on_this_port():
    assert funnel_from_config(config(), 8000) == f"https://{HOST}"


def test_other_listen_port_is_kept():
    assert funnel_from_config(config(listen=8443), 8000) == f"https://{HOST}:8443"


def test_serve_without_funnel_or_other_port_is_none():
    assert funnel_from_config(config(funnel=False), 8000) is None
    assert funnel_from_config(config(port=9000), 8000) is None
    assert funnel_from_config({}, 8000) is None
