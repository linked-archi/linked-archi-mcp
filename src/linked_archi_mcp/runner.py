"""Run a skill owner as a subprocess of this interpreter."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import anyio

from .skills import ENV_SKILLS_DIR, Skills

#: Environment the skills read that the server decides explicitly instead. Leaving them
#: inherited would let a variable in the server's environment silently change what a
#: tool call targets, which is the opposite of what an explicit tool argument promises.
_SCRUBBED = ("LINKED_ARCHI_DATA",)


class RunTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class Completed:
    owner: str
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Runner:
    def __init__(self, skills: Skills, *, timeout_s: float = 120.0,
                 cwd: Path | None = None) -> None:
        self.skills = skills
        self.timeout_s = timeout_s
        self.cwd = cwd

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED}
        # Authoritative in every owner, so companions resolve to the same tree the
        # server resolved, never to a stray copy on PATH.
        env[ENV_SKILLS_DIR] = str(self.skills.root)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    async def run(self, owner: str, args: Sequence[str], *,
                  stdin: str | None = None) -> Completed:
        argv = [sys.executable, str(self.skills.script(owner)), *args]
        try:
            with anyio.fail_after(self.timeout_s):
                done = await anyio.run_process(
                    argv,
                    input=stdin.encode("utf-8") if stdin is not None else b"",
                    check=False,
                    env=self.env(),
                    cwd=self.cwd,
                )
        except TimeoutError as exc:
            raise RunTimeout(
                f"{owner} did not finish within {self.timeout_s:g}s and was stopped."
            ) from exc
        return Completed(
            owner=owner,
            args=tuple(args),
            returncode=done.returncode,
            stdout=done.stdout.decode("utf-8", errors="replace"),
            stderr=done.stderr.decode("utf-8", errors="replace"),
        )
