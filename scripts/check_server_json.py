#!/usr/bin/env python3
"""Validate server.json against the MCP Registry schema it declares in ``$schema``.

    python scripts/check_server_json.py                 # downloads the declared schema
    python scripts/check_server_json.py --schema FILE   # offline, against a saved copy

The schema is fetched rather than vendored, so the check always runs against exactly the
version the file claims to follow, and a schema bump is one edit to server.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("server_json", nargs="?", type=Path, default=ROOT / "server.json")
    parser.add_argument("--schema", type=Path, help="a saved copy of the schema")
    args = parser.parse_args(argv)

    document = json.loads(args.server_json.read_text(encoding="utf-8"))
    if args.schema:
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
    else:
        url = document["$schema"]
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - https URL from our own file
            schema = json.load(response)
    validator = jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    for error in errors:
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        print(f"{args.server_json.name}: {where}: {error.message}", file=sys.stderr)
    if errors:
        return 1
    print(f"{args.server_json.name} is valid against {document['$schema']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
