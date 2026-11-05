"""Facts a release repeats in several files, checked to agree, without building anything.

The build itself, a clean install of the wheel and the container image are exercised in CI
by scripts/smoke.py; server.json is validated against the registry schema there too.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from linked_archi_mcp import __version__
from linked_archi_mcp.skills import BUNDLED, OWNERS

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_one_version_everywhere():
    assert PYPROJECT["project"]["dynamic"] == ["version"]
    assert PYPROJECT["tool"]["hatch"]["version"]["path"] == "src/linked_archi_mcp/__init__.py"
    assert SERVER["version"] == __version__
    assert [p["version"] for p in SERVER["packages"]] == [__version__]


def test_registry_package_is_this_project():
    (package,) = SERVER["packages"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == PYPROJECT["project"]["name"]
    assert package["registryBaseUrl"] == "https://pypi.org"
    assert package["transport"] == {"type": "stdio"}
    named = {a["name"] for a in package.get("packageArguments", []) if a["type"] == "named"}
    flags = {"--data", "--endpoint", "--profile"}
    assert named <= flags, f"server.json names flags the CLI lacks: {named - flags}"


def test_server_name_is_proven_where_the_registry_looks():
    name = SERVER["name"]
    assert name == "io.github.linked-archi/linked-archi-mcp"
    # PyPI: the README is the long description. The marker must end at a boundary.
    assert re.search(rf"mcp-name: {re.escape(name)}(\s|-->)", README)
    # OCI: the image label, ready for when the image is listed too.
    assert f'io.modelcontextprotocol.server.name="{name}"' in DOCKERFILE


def test_registry_text_limits():
    assert 1 <= len(SERVER["description"]) <= 100
    assert 1 <= len(SERVER.get("title", "x")) <= 100


def test_wheel_bundles_the_vendored_skills():
    forced = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert forced == {"vendor/linked-archi-apm/skills": "linked_archi_mcp/_skills"}
    assert BUNDLED.name == "_skills" and BUNDLED.parent.name == "linked_archi_mcp"
    source = ROOT / "vendor/linked-archi-apm/skills"
    for skill, executable in OWNERS.values():
        assert (source / skill / "scripts" / executable).is_file(), skill
    for licence in PYPROJECT["project"]["license-files"]:
        assert (ROOT / licence).is_file(), licence


def test_sdist_carries_what_the_wheel_is_built_from():
    included = set(PYPROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"])
    assert "vendor/linked-archi-apm/skills" in included
    assert {"vendor/linked-archi-apm/LICENSE", "vendor/linked-archi-apm/NOTICE"} <= included


def _notes(*args: str, changelog: Path | None = None) -> subprocess.CompletedProcess[str]:
    extra = ["--changelog", str(changelog)] if changelog else []
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "release_notes.py"),
                           *args, *extra], capture_output=True, text=True)


def test_this_version_has_release_notes():
    done = _notes(__version__)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() and not done.stdout.startswith("## ")


@pytest.mark.parametrize("heading,require_date,code", [
    ("## 1.2.3 - 2026-11-20", True, 0),
    ("## 1.2.3 - unreleased", False, 0),
    ("## 1.2.3 - unreleased", True, 1),
    ("## 1.2.3", True, 1),
    ("## 9.9.9 - 2026-11-20", False, 1),
])
def test_release_notes_rules(tmp_path, heading, require_date, code):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(f"# Changelog\n\n{heading}\n\n- a change\n\n## 1.0.0 - 2026-01-01\n\n- old\n")
    args = ["v1.2.3"] + (["--require-date"] if require_date else [])
    done = _notes(*args, changelog=changelog)
    assert done.returncode == code, done.stderr
    if code == 0:
        assert done.stdout.strip() == "- a change"
