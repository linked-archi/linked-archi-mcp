from __future__ import annotations

from pathlib import Path

import pytest

from linked_archi_mcp.skills import ENV_SKILLS_DIR, VENDORED, SkillsNotFound, locate


def test_vendored_is_the_fallback():
    skills = locate(env={})
    assert skills.root == VENDORED
    assert skills.origin == "vendored submodule"


def test_env_wins_over_vendored_and_never_falls_back(tmp_path: Path):
    with pytest.raises(SkillsNotFound, match="missing"):
        locate(env={ENV_SKILLS_DIR: str(tmp_path)})


def test_explicit_wins_over_env(tmp_path: Path):
    skills = locate(VENDORED, env={ENV_SKILLS_DIR: str(tmp_path)})
    assert skills.origin == "--skills-dir"


def test_versions_and_documents(skills):
    assert skills.version("query")
    docs = skills.documents()
    uris = {d.uri for d in docs}
    assert "linked-archi://skills/linked-archi-query/SKILL.md" in uris
    assert "linked-archi://skills/linked-archi-query/references/safety.md" in uris
    assert all(d.path.is_file() for d in docs)
