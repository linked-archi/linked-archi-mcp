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
