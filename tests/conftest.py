from __future__ import annotations

from pathlib import Path

import pytest

from linked_archi_mcp.config import Config, Target
from linked_archi_mcp.skills import VENDORED, Skills, locate

FIXTURES = VENDORED.parent / "fixtures"
BASE = FIXTURES / "base.trig"

if not VENDORED.is_dir():  # pragma: no cover - a checkout without its submodule
    pytest.exit("vendor/linked-archi-apm is missing: run `git submodule update --init`",
                returncode=2)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def skills() -> Skills:
    return locate()


@pytest.fixture
def config() -> Config:
    return Config(target=Target((str(BASE),)))


@pytest.fixture
def tmp_rdf(tmp_path: Path):
    def write(name: str, text: str) -> str:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return str(path)
    return write
