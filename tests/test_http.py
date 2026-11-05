from __future__ import annotations

import pytest

from linked_archi_mcp.http import BearerTokenMiddleware, is_loopback


async def _call(app, headers):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    await app({"type": "http", "headers": headers}, receive, send)
    return sent


async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.anyio
async def test_bearer_token_required():
    app = BearerTokenMiddleware(_ok, "s3cret")
    assert (await _call(app, []))[0]["status"] == 401
    assert (await _call(app, [(b"authorization", b"Bearer nope")]))[0]["status"] == 401
    assert (await _call(app, [(b"authorization", b"Bearer s3cret")]))[0]["status"] == 200


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("[::1]", True),
    ("0.0.0.0", False), ("10.0.0.5", False), ("example.org", False),
])
def test_loopback(host, expected):
    assert is_loopback(host) is expected


def _scope(method: str, path: str, headers=()):
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 50000),
            "headers": [(b"host", b"127.0.0.1:8000"), *headers]}


async def _request(app, method: str, path: str, headers=()):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(_scope(method, path, headers), receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


@pytest.fixture
def mcp_server(config, skills):
    from linked_archi_mcp.server import build_server
    return build_server(config, skills)


@pytest.mark.anyio
async def test_healthz_is_public_and_says_nothing_else(mcp_server):
    from linked_archi_mcp.http import build_app

    app = build_app(mcp_server, host="127.0.0.1", path="/mcp", token="s3cret",
                    allowed_hosts=None, stateless=False)
    assert await _request(app, "GET", "/healthz") == (200, b"ok")
    status, _ = await _request(app, "POST", "/mcp")
    assert status == 401
    # The exemption is the exact path, not a prefix.
    status, _ = await _request(app, "GET", "/healthz/../mcp")
    assert status == 401


def test_health_path_is_reserved(mcp_server, capsys):
    from linked_archi_mcp.cli import main
    from linked_archi_mcp.http import build_app

    with pytest.raises(ValueError, match="reserved"):
        build_app(mcp_server, host="127.0.0.1", path="/healthz", token=None,
                  allowed_hosts=None, stateless=False)
    assert main(["http", "--path", "/healthz", "--data", "g.trig"]) == 2
    assert "reserved" in capsys.readouterr().err
