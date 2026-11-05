"""Command line: ``linked-archi-mcp [stdio|http] ...``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Sequence

from . import __version__
from .config import STORE_MODES, Config, ConfigError, Target, data_from_env
from .skills import ENV_SKILLS_DIR, SkillsNotFound, locate

EPILOG = f"""\
Skills are found from --skills-dir, then ${ENV_SKILLS_DIR}, then the vendored
submodule of a source checkout. The dataset comes from --data/--endpoint, else
$LINKED_ARCHI_DATA ({os.pathsep}-separated); tools may also name one per call when
client paths are allowed (the stdio default).

Examples:
  linked-archi-mcp --data dist/merged.trig
  linked-archi-mcp --endpoint https://graph.example.org/sparql --profile linked-archi-merged
  LINKED_ARCHI_MCP_TOKEN=... linked-archi-mcp http --host 0.0.0.0 --data graph.trig
"""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="linked-archi-mcp",
        description="MCP server for read-only analysis of Linked.Archi architecture graphs.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("transport", nargs="?", choices=("stdio", "http"), default="stdio",
                   help="stdio (default) for a local client; http for streamable HTTP")
    g = p.add_argument_group("dataset and profile")
    g.add_argument("--data", action="append", default=[], metavar="FILE",
                   help="local RDF file (repeatable; files merge into one dataset)")
    g.add_argument("--endpoint", metavar="URL", help="HTTPS SPARQL endpoint instead of files")
    g.add_argument("--profile", metavar="NAME_OR_PATH",
                   help="graph profile (default: the skills' default, linked-archi-default)")
    g.add_argument("--catalog", action="append", default=[], metavar="PATH",
                   help="trusted project query catalogue (repeatable)")
    g.add_argument("--shapes", action="append", default=[], metavar="FILE",
                   help="default SHACL shapes for validate_run (repeatable)")
    g.add_argument("--store", choices=STORE_MODES, help="local store mode (see connect skill)")
    g.add_argument("--timeout-ms", type=int, metavar="MS", help="endpoint request deadline")
    g = p.add_argument_group("server")
    g.add_argument("--skills-dir", metavar="DIR", help="directory holding linked-archi-* skills")
    g.add_argument("--allow-client-paths", dest="allow_client_paths", action="store_true",
                   default=None, help="let tool calls name files, endpoints and profile "
                   "paths (default: on for stdio, off for http)")
    g.add_argument("--no-client-paths", dest="allow_client_paths", action="store_false",
                   help="only ever use the configured dataset, shapes and bundled profiles")
    g.add_argument("--max-rows", type=int, default=200, metavar="N",
                   help="rows returned per query result by default (default: 200)")
    g.add_argument("--run-timeout", type=float, default=120.0, metavar="SECONDS",
                   help="deadline for one skill invocation (default: 120)")
    g.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"))
    g = p.add_argument_group("http transport")
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=8000)
    g.add_argument("--path", default="/mcp", help="endpoint path (default: /mcp)")
    g.add_argument("--allowed-host", action="append", default=[], metavar="HOST[:PORT]",
                   help="Host header to accept (DNS-rebinding protection; repeatable)")
    g.add_argument("--stateless", action="store_true", help="stateless streamable HTTP")
    g.add_argument("--insecure-no-auth", action="store_true",
                   help="allow a non-loopback bind without $LINKED_ARCHI_MCP_TOKEN")
    return p


def build_config(args: argparse.Namespace) -> Config:
    data = tuple(os.path.abspath(os.path.expanduser(d)) for d in args.data)
    if not data and not args.endpoint:
        data = data_from_env()
    allow = args.allow_client_paths
    if allow is None:
        allow = args.transport == "stdio"
    return Config(
        target=Target(data, args.endpoint),
        profile=args.profile,
        catalogs=tuple(os.path.abspath(c) for c in args.catalog),
        shapes=tuple(os.path.abspath(s) for s in args.shapes),
        store=args.store,
        timeout_ms=args.timeout_ms,
        allow_client_paths=allow,
        max_rows=args.max_rows,
        run_timeout_s=args.run_timeout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    # stdout is the stdio transport: everything human-facing goes to stderr.
    logging.basicConfig(level=args.log_level, stream=sys.stderr)
    try:
        skills = locate(args.skills_dir)
        config = build_config(args)
    except (SkillsNotFound, ConfigError) as exc:
        print(f"linked-archi-mcp: {exc}", file=sys.stderr)
        return 2

    from .server import build_server

    server = build_server(config, skills)
    if args.transport == "stdio":
        server.run("stdio")
        return 0

    from .http import ENV_TOKEN, HEALTH_PATH, is_loopback, serve

    if args.path == HEALTH_PATH:
        print(f"linked-archi-mcp: --path {HEALTH_PATH} is reserved for the health check.",
              file=sys.stderr)
        return 2
    token = os.environ.get(ENV_TOKEN) or None
    if not token and not is_loopback(args.host) and not args.insecure_no_auth:
        print(f"linked-archi-mcp: refusing to bind {args.host} without ${ENV_TOKEN}. "
              "Set a token, bind loopback, or pass --insecure-no-auth behind a gateway "
              "that authenticates.", file=sys.stderr)
        return 2
    if args.allow_client_paths:
        logging.getLogger(__name__).warning(
            "HTTP clients may name local files and endpoints (--allow-client-paths).")
    serve(server, host=args.host, port=args.port, path=args.path, token=token,
          allowed_hosts=args.allowed_host or None, stateless=args.stateless,
          log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
