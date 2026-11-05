"""Locate the linked-archi-apm skills this server wraps.

The server never imports a skill's Python package. Each skill is an owner with its own
executable and versioned JSON contracts, and the server talks to it the way sibling
skills talk to each other: as a subprocess. That keeps read-only enforcement, refusal
semantics and provenance in the one place the skills already put them.

Resolution order, first match wins and never falls back:

1. an explicit ``--skills-dir``;
2. ``$LINKED_ARCHI_SKILLS_DIR``, the same variable the skills use to find each other;
3. the ``vendor/linked-archi-apm`` git submodule of a source checkout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

ENV_SKILLS_DIR = "LINKED_ARCHI_SKILLS_DIR"

#: owner key -> (skill directory, executable)
OWNERS: dict[str, tuple[str, str]] = {
    "source": ("linked-archi-source", "la-source"),
    "profile": ("linked-archi-profile", "la-profile"),
    "connect": ("linked-archi-connect", "la-connect"),
    "query": ("linked-archi-query", "la-query"),
    "analyse": ("linked-archi-analyse", "la-analyse"),
    "validate": ("linked-archi-validate", "la-validate"),
}

#: Owners every tool path needs. Source and validate are optional companions.
REQUIRED = ("profile", "connect", "query", "analyse")

VENDORED = Path(__file__).resolve().parents[2] / "vendor" / "linked-archi-apm" / "skills"


class SkillsNotFound(RuntimeError):
    """The skills directory could not be resolved, or lacks a required owner."""


@dataclass(frozen=True)
class Document:
    """A markdown file a skill ships as agent context."""

    uri: str
    path: Path
    skill: str
    relpath: str


@dataclass(frozen=True)
class Skills:
    root: Path
    origin: str

    def script(self, owner: str) -> Path:
        skill, executable = OWNERS[owner]
        path = self.root / skill / "scripts" / executable
        if not path.is_file():
            raise SkillsNotFound(
                f"Skill {skill} is not installed under {self.root} ({self.origin})."
            )
        return path

    def has(self, owner: str) -> bool:
        skill, executable = OWNERS[owner]
        return (self.root / skill / "scripts" / executable).is_file()

    def version(self, owner: str) -> str | None:
        """The ``metadata.version`` a skill declares in its SKILL.md frontmatter."""
        skill, _ = OWNERS[owner]
        text = _read(self.root / skill / "SKILL.md")
        if not text or not text.startswith("---"):
            return None
        _, _, rest = text.partition("---")
        front, _, _ = rest.partition("\n---")
        try:
            meta = yaml.safe_load(front) or {}
        except yaml.YAMLError:
            return None
        version = (meta.get("metadata") or {}).get("version")
        return str(version) if version is not None else None

    def documents(self) -> list[Document]:
        """Every SKILL.md and reference document, in a stable order."""
        found: list[Document] = []
        for owner in OWNERS:
            skill, _ = OWNERS[owner]
            base = self.root / skill
            if not base.is_dir():
                continue
            candidates = [base / "SKILL.md"]
            references = base / "references"
            if references.is_dir():
                candidates += sorted(references.rglob("*.md"))
            for path in candidates:
                if path.is_file():
                    rel = path.relative_to(base).as_posix()
                    found.append(
                        Document(uri=f"linked-archi://skills/{skill}/{rel}",
                                 path=path.resolve(), skill=skill, relpath=rel)
                    )
        return found


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def locate(explicit: str | os.PathLike[str] | None = None,
           env: Mapping[str, str] | None = None) -> Skills:
    env = os.environ if env is None else env
    if explicit:
        skills = Skills(Path(explicit).expanduser().resolve(), "--skills-dir")
    elif env.get(ENV_SKILLS_DIR):
        skills = Skills(Path(env[ENV_SKILLS_DIR]).expanduser().resolve(), f"${ENV_SKILLS_DIR}")
    elif VENDORED.is_dir():
        skills = Skills(VENDORED, "vendored submodule")
    else:
        raise SkillsNotFound(
            "No linked-archi-apm skills found. Pass --skills-dir, set "
            f"${ENV_SKILLS_DIR} to the directory holding the linked-archi-* skills, or "
            "run from a checkout with `git submodule update --init`."
        )
    missing = [OWNERS[o][0] for o in REQUIRED if not skills.has(o)]
    if missing:
        raise SkillsNotFound(
            f"Skills directory {skills.root} ({skills.origin}) is missing: "
            + ", ".join(missing)
        )
    return skills
