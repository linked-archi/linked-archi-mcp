from __future__ import annotations

import pytest

from linked_archi_mcp.evidence import EvidenceError, EvidenceStore


def test_bounded_and_oldest_evicted(tmp_path):
    store = EvidenceStore(tmp_path, keep=2)
    first = store.save("a", "{}")
    first_path = store.path(first)
    second, third = store.save("b", "{}"), store.save("c", "{}")
    assert not first_path.exists()
    with pytest.raises(EvidenceError, match="evicted"):
        store.path(first)
    assert store.path(second).is_file() and store.path(third).is_file()


def test_scratch_is_removed(tmp_path):
    store = EvidenceStore(tmp_path)
    with store.scratch() as scratch:
        (scratch / "x").write_text("1")
    assert not scratch.exists()


def test_owned_store_is_removed_on_close():
    store = EvidenceStore()
    store.save("a", "{}")
    store.close()
    assert not store.root.exists()
