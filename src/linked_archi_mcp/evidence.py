"""Result envelopes kept on disk so an investigation can be bundled afterwards.

Every answered query is saved exactly as the query owner produced it, the same document
``la-query --json -o`` writes, under an opaque ``evidence_id``. ``analyse_bundle`` then
hands those files to the analyse owner in the order the client cites them. The server
never edits an envelope: a bundle has to be reproducible from what the query owner said,
not from a copy the server reshaped.

The store is bounded. Oldest envelopes are evicted first, and an evicted id is reported
as unknown rather than silently dropped from a bundle.
"""

from __future__ import annotations

import re
import secrets
import shutil
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_ID = re.compile(r"^e-[0-9a-f]{12}$")


class EvidenceError(KeyError):
    pass


class EvidenceStore:
    def __init__(self, root: Path | None = None, *, keep: int = 500) -> None:
        if keep < 1:
            raise ValueError("keep must be positive")
        self._owned = root is None
        self.root = Path(tempfile.mkdtemp(prefix="linked-archi-mcp-")) if root is None else root
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self._entries: OrderedDict[str, Path] = OrderedDict()

    def new_path(self, label: str) -> tuple[str, Path]:
        """Reserve an id and the file its envelope is to be written to."""
        evidence_id = f"e-{secrets.token_hex(6)}"
        slug = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")[:60] or "query"
        return evidence_id, self.root / f"{evidence_id}-{slug}.json"

    def commit(self, evidence_id: str, path: Path) -> None:
        """Record a written envelope, evicting the oldest past the bound."""
        self._entries[evidence_id] = path
        while len(self._entries) > self.keep:
            _, old = self._entries.popitem(last=False)
            old.unlink(missing_ok=True)

    def save(self, label: str, text: str) -> str:
        evidence_id, path = self.new_path(label)
        path.write_text(text, encoding="utf-8")
        self.commit(evidence_id, path)
        return evidence_id

    def path(self, evidence_id: str) -> Path:
        if not _ID.match(evidence_id) or evidence_id not in self._entries:
            raise EvidenceError(
                f"Unknown evidence_id {evidence_id!r}: it was never issued by this server "
                "process, or it was evicted. Re-run the query to get a fresh one."
            )
        return self._entries[evidence_id]

    @contextmanager
    def scratch(self) -> Iterator[Path]:
        """A private directory for one request's files, removed when the request ends."""
        path = Path(tempfile.mkdtemp(prefix="req-", dir=self.root))
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def close(self) -> None:
        if self._owned:
            shutil.rmtree(self.root, ignore_errors=True)
