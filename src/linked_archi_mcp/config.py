"""Server configuration: what the server targets, and what a client may change."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

STORE_MODES = ("memory", "cached", "readonly", "refresh")

#: A bundled profile name, or ``examples/<name>``. Anything else is a path.
_BUNDLED_PROFILE = re.compile(r"^(examples/)?[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    """Exactly one of local RDF files or a SPARQL endpoint, or nothing yet."""

    data: tuple[str, ...] = ()
    endpoint: str | None = None

    def __post_init__(self) -> None:
        if self.data and self.endpoint:
            raise ConfigError("Give local data or an endpoint, not both.")

    @property
    def empty(self) -> bool:
        return not self.data and not self.endpoint

    def args(self) -> list[str]:
        if self.endpoint:
            return ["--endpoint", self.endpoint]
        out: list[str] = []
        for path in self.data:
            out += ["--data", path]
        return out

    def describe(self) -> dict[str, object]:
        return {"data": list(self.data), "endpoint": self.endpoint}


@dataclass(frozen=True)
class Config:
    target: Target = field(default_factory=Target)
    profile: str | None = None
    catalogs: tuple[str, ...] = ()
    shapes: tuple[str, ...] = ()
    store: str | None = None
    timeout_ms: int | None = None
    #: Whether tool arguments may name local files, endpoints or profile paths. On for
    #: stdio, where the client is the user's own agent. Off by default over HTTP, where
    #: it would let any caller read files or reach hosts as the server.
    allow_client_paths: bool = True
    max_rows: int = 200
    run_timeout_s: float = 120.0

    def __post_init__(self) -> None:
        if self.store is not None and self.store not in STORE_MODES:
            raise ConfigError(f"Unknown store mode {self.store!r}; use one of {STORE_MODES}.")
        if self.max_rows < 1:
            raise ConfigError("max_rows must be positive.")

    # -- resolution of per-call overrides ---------------------------------------------

    def target_for(self, data: list[str] | None, endpoint: str | None) -> Target:
        if not data and not endpoint:
            if self.target.empty:
                raise ConfigError(
                    "No dataset. Pass `data` (local RDF files) or `endpoint`, or start "
                    "the server with --data/--endpoint."
                )
            return self.target
        if not self.allow_client_paths:
            raise ConfigError(
                "This server does not accept datasets from clients; it answers over "
                f"its configured target {self.target.describe()}. Omit data/endpoint."
            )
        return Target(tuple(_absolute(p) for p in (data or [])), endpoint or None)

    def profile_for(self, profile: str | None) -> str | None:
        if not profile:
            return self.profile
        if not self.allow_client_paths and not is_bundled_profile(profile):
            raise ConfigError(
                f"Profile {profile!r} is a path. This server accepts only bundled "
                "profile names from clients (see profile_list)."
            )
        return profile

    def paths_for(self, label: str, paths: list[str] | None,
                  default: tuple[str, ...]) -> tuple[str, ...]:
        if not paths:
            return default
        if not self.allow_client_paths:
            raise ConfigError(
                f"This server does not accept {label} paths from clients; it uses its "
                "configured ones."
            )
        return tuple(_absolute(p) for p in paths)

    def query_args(self) -> list[str]:
        out: list[str] = []
        for catalog in self.catalogs:
            out += ["--catalog", catalog]
        return out

    def connect_args(self, target: Target, *, store: bool = True) -> list[str]:
        """Target flags for an owner; ``store`` only where that command takes --store."""
        out = target.args()
        if target.endpoint and self.timeout_ms:
            out += ["--timeout-ms", str(self.timeout_ms)]
        if store and target.data and self.store:
            out += ["--store", self.store]
        return out


def is_bundled_profile(name: str) -> bool:
    return bool(_BUNDLED_PROFILE.match(name)) and not name.endswith((".yaml", ".yml"))


def _absolute(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def data_from_env(env: dict[str, str] | None = None) -> tuple[str, ...]:
    """``$LINKED_ARCHI_DATA``, split the way the connect skill splits it."""
    raw = (os.environ if env is None else env).get("LINKED_ARCHI_DATA", "")
    return tuple(_absolute(p) for p in raw.split(os.pathsep) if p.strip())
