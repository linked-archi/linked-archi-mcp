#!/usr/bin/env python3
"""Print one version's CHANGELOG section: the body of its GitHub release.

    python scripts/release_notes.py 0.1.0
    python scripts/release_notes.py 0.1.0 --require-date    # what the release workflow runs

Exit 1 when the section is missing or empty, or, with --require-date, still headed
"unreleased": a release whose notes were never finished is caught before anything is
published, not after.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^## +(?P<version>\S+)(?: +- +(?P<label>.+?))?\s*$")


def section(text: str, version: str) -> tuple[str | None, str] | None:
    """(label, body) for ``## <version>``, or None when there is no such heading."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if match and match["version"] == version:
            body: list[str] = []
            for following in lines[index + 1:]:
                if following.startswith("## "):
                    break
                body.append(following)
            return match["label"], "\n".join(body).strip()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="e.g. 0.1.0 (a leading v is ignored)")
    parser.add_argument("--changelog", type=Path,
                        default=Path(__file__).resolve().parents[1] / "CHANGELOG.md")
    parser.add_argument("--require-date", action="store_true",
                        help="refuse a section still headed 'unreleased'")
    args = parser.parse_args(argv)
    version = args.version.removeprefix("v")
    found = section(args.changelog.read_text(encoding="utf-8"), version)
    if found is None:
        print(f"{args.changelog.name} has no '## {version}' section.", file=sys.stderr)
        return 1
    label, body = found
    if not body:
        print(f"The '## {version}' section of {args.changelog.name} is empty.", file=sys.stderr)
        return 1
    if args.require_date and (label is None or "unreleased" in label.lower()):
        print(f"'## {version}' is not dated. Head it '## {version} - YYYY-MM-DD' before tagging.",
              file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
