"""Streamable HTTP transport, with an optional static bearer token.

Binding beyond loopback without a token is refused unless explicitly forced: a reachable
server answering questions about an organisation's architecture is a disclosure, read-only
or not. For anything beyond a shared secret, put the server behind a gateway that does
real authentication.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
from typing import Any, Awaitable, Callable, Iterable

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

ENV_TOKEN = "LINKED_ARCHI_MCP_TOKEN"

#: Liveness for load balancers and orchestrators. Unauthenticated by design, so it says
#: nothing but "ok": no version, no dataset, nothing a scanner could use.
HEALTH_PATH = "/healthz"

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BearerTokenMiddleware:
    """Reject HTTP requests whose ``Authorization`` is not ``Bearer <token>``.

    ``public_paths`` are matched exactly against the request path and pass unchecked.
    """

    def __init__(self, app: ASGIApp, token: str, *, public_paths: Iterable[str] = ()) -> None:
        if not token:
            raise ValueError("token must be non-empty")
        self.app = app
        self._expected = f"Bearer {token}".encode("utf-8")
        self._public = frozenset(public_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") in self._public:
            await self.app(scope, receive, send)
            return
        supplied = b""
        for key, value in scope.get("headers") or []:
            if key.lower() == b"authorization":
                supplied = value
                break
        if not hmac.compare_digest(supplied, self._expected):
            body = json.dumps({"error": "unauthorized"}).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b'Bearer realm="linked-archi-mcp"'),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


async def _healthz(request: Request) -> Response:
    return PlainTextResponse("ok")


def build_app(server: MCPServer, *, host: str, path: str, token: str | None,
              allowed_hosts: list[str] | None, stateless: bool) -> ASGIApp:
    if path == HEALTH_PATH:
        raise ValueError(f"{HEALTH_PATH} is reserved for the health check")
    server.custom_route(HEALTH_PATH, methods=["GET"], include_in_schema=False)(_healthz)
    security = None
    if allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[f"{scheme}://{h}" for h in allowed_hosts
                             for scheme in ("http", "https")],
        )
    app: ASGIApp = server.streamable_http_app(
        streamable_http_path=path,
        stateless_http=stateless,
        transport_security=security,
        host=host,
    )
    if token:
        app = BearerTokenMiddleware(app, token, public_paths=(HEALTH_PATH,))
    return app


def serve(server: MCPServer, *, host: str, port: int, path: str, token: str | None,
          allowed_hosts: list[str] | None, stateless: bool, log_level: str) -> None:
    app = build_app(server, host=host, path=path, token=token,
                    allowed_hosts=allowed_hosts, stateless=stateless)
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())
