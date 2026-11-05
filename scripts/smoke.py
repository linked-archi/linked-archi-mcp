#!/usr/bin/env python3
"""End-to-end smoke test of an *installed* linked-archi-mcp, run from outside the checkout.

    python -I scripts/smoke.py stdio --data FILE                  # the console script beside
                                                                  # this interpreter
    python -I scripts/smoke.py stdio --data FILE -- docker run -i --rm ... IMAGE --data FILE
    python -I scripts/smoke.py http URL --token TOKEN             # a running server

It checks what a release has to get right and the unit tests cannot see: the skills are
found where an installation keeps them, a template answers, a refusal comes back as one,
a batch runs and bundles. Over HTTP it also checks the token is enforced and /healthz
answers without it. ``-I`` keeps the checkout off sys.path, so only the installed package
can be imported.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

import anyio
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

SHAPES = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix ex: <https://example.org/> .
ex:ThingShape a sh:NodeShape ; sh:targetClass ex:Thing ;
  sh:property [ sh:path ex:name ; sh:minCount 1 ] .
"""
CONFORMING = """\
@prefix ex: <https://example.org/> .
ex:a a ex:Thing ; ex:name "A" .
"""
FINDINGS = {
    "answer": "smoke",
    "graph_facts": [{"claim": "Models are loaded.", "steps": [1]}],
    "derived_facts": [], "document_statements": [], "inferences": [],
    "unknowns": [{"claim": "Nothing else was asked."}],
}


class SmokeFailure(AssertionError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def ok(message: str) -> None:
    print(f"ok    {message}")


async def call(client: Client, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    result = await client.call_tool(tool, args or {})
    text = " ".join(getattr(c, "text", "") for c in result.content)
    expect(not result.is_error, f"{tool} failed: {text}")
    expect(isinstance(result.structured_content, dict), f"{tool} returned no structured content")
    return result.structured_content


async def exercise(client: Client, *, origin: str, client_paths: bool,
                   shares_files: bool) -> None:
    """``shares_files``: the server can read files this process writes (not a container)."""
    doctor = await call(client, "doctor")
    expect(doctor["skills_origin"] == origin,
           f"skills came from {doctor['skills_origin']!r} ({doctor['skills_root']}), expected {origin!r}")
    versions = {owner: s["version"] for owner, s in doctor["skills"].items() if s["installed"]}
    expect(len(versions) == 6, f"expected all six skills, found {sorted(versions)}")
    expect(None not in versions.values() and len(set(versions.values())) == 1,
           f"skills disagree on their version: {versions}")
    expect(doctor["python_packages"]["pyoxigraph"], "pyoxigraph is not installed")
    expect(doctor["accepts_client_paths"] is client_paths,
           f"accepts_client_paths is {doctor['accepts_client_paths']}, expected {client_paths}")
    ok(f"server {doctor['server_version']}, skills {versions['query']} ({origin})")

    answered = await call(client, "query_run", {"template": "core/models"})
    expect(answered["status"] == "answered" and answered["row_count"] > 0,
           f"core/models did not answer: {answered}")
    ok(f"query_run answered {answered['row_count']} row(s) from {answered['dataset_id']}")

    refused = await call(client, "query_run", {"template": "core/dependents-direct",
                                               "profile": "linked-archi-qualified"})
    expect(refused["status"] == "refused" and refused["alternatives"],
           f"expected a refusal with alternatives: {refused}")
    ok(f"refusal names {', '.join(refused['alternatives'])}")

    batch = await call(client, "query_batch", {"queries": [
        {"template": "core/models"}, {"template": "core/inventory-summary"}]})
    expect(batch["status"] == "answered" and batch["count"] == 2, f"batch failed: {batch}")
    ok("query_batch answered 2 queries in one load")

    bundle = await call(client, "analyse_bundle", {
        "evidence_ids": [r["evidence_id"] for r in batch["results"]],
        "question": "What is loaded?", "findings": FINDINGS})
    expect(bundle["status"] == "bundled" and "What is loaded?" in bundle["markdown"],
           f"bundle failed: {bundle}")
    ok("analyse_bundle rendered the evidence")

    if client_paths and shares_files and doctor["python_packages"]["pyshacl"]:
        with tempfile.TemporaryDirectory() as tmp:
            shapes, data = Path(tmp, "shapes.ttl"), Path(tmp, "data.ttl")
            shapes.write_text(SHAPES)
            data.write_text(CONFORMING)
            result = await call(client, "validate_run", {"shapes": [str(shapes)], "data": [str(data)]})
        expect(result["status"] == "conforms", f"validate_run: {result}")
        ok("validate_run conforms (pyshacl)")


async def smoke_stdio(args: argparse.Namespace) -> None:
    if args.command:
        command, *argv = args.command
    else:
        command = str(Path(sys.executable).parent / "linked-archi-mcp")
        argv = ["--data", str(Path(args.data).resolve())]
    async with Client(StdioServerParameters(command=command, args=argv)) as client:
        # stdio servers accept client paths by default; a command may be a container,
        # which cannot see this machine's temporary files.
        await exercise(client, origin=args.expect_origin, client_paths=True,
                       shares_files=not args.command)


async def smoke_http(args: argparse.Namespace) -> None:
    base = args.url.rsplit("/", 1)[0]
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(30, read=300)) as anonymous:
        health = await anonymous.get(f"{base}/healthz")
        expect(health.status_code == 200 and health.text == "ok",
               f"/healthz answered {health.status_code}: {health.text!r}")
        ok("/healthz answers without the token")
        denied = await anonymous.post(args.url, json={})
        expect(denied.status_code == 401, f"no token answered {denied.status_code}, expected 401")
        ok("a request without the token is refused (401)")
    headers = {"Authorization": f"Bearer {args.token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=300)) as http:
        async with Client(streamable_http_client(args.url, http_client=http)) as client:
            await exercise(client, origin=args.expect_origin, client_paths=False,
                           shares_files=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expect-origin", default="bundled",
                        help="where doctor must report the skills came from (default: bundled)")
    modes = parser.add_subparsers(dest="mode", required=True)
    stdio = modes.add_parser("stdio")
    stdio.add_argument("--data", help="RDF file for the launched server")
    stdio.add_argument("command", nargs=argparse.REMAINDER,
                       help="after --, a command that starts the server on stdio")
    http = modes.add_parser("http")
    http.add_argument("url", help="the MCP endpoint, e.g. http://127.0.0.1:8000/mcp")
    http.add_argument("--token", required=True)
    args = parser.parse_args()
    if args.mode == "stdio":
        args.command = list(args.command or [])
        if args.command[:1] == ["--"]:
            args.command = args.command[1:]
        if not args.command and not args.data:
            parser.error("stdio needs --data, or a command after --")
    try:
        anyio.run(smoke_stdio if args.mode == "stdio" else smoke_http, args)
    except SmokeFailure as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    print("smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
