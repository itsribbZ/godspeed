#!/usr/bin/env python3
"""
Homer L5 — MNEMOS smoke tests
============================
Stdlib-only tests. Uses temp dirs so no pollution of real mnemos state.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))
from mnemos import (  # noqa: E402
    MnemosStore,
    CoreEntry,
    validate_citation,
    require_citation,
    CitationError,
)

PASS = "[PASS]"
FAIL = "[FAIL]"


def _fresh_store(budget: int = 500) -> tuple[MnemosStore, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="mnemos_test_"))
    store = MnemosStore(
        core_file=tmp / "core" / "core_memory.md",
        recall_db=tmp / "recall" / "recall.db",
        archival_dir=tmp / "archival",
        budget_tokens=budget,
    )
    return store, tmp


# ── Citation validation ─────────────────────────────────────────────────────

def test_citation_file_line() -> bool:
    return validate_citation("Toke/research/brain.md:42")


def test_citation_file_range() -> bool:
    return validate_citation("brain_cli.py:100-150")


def test_citation_url() -> bool:
    return validate_citation("https://anthropic.com/docs")


def test_citation_arxiv() -> bool:
    return validate_citation("arxiv:2603.18897")


def test_citation_mnemos_back_pointer() -> bool:
    return validate_citation("mnemos:archival_xyz123")


def test_citation_session() -> bool:
    return validate_citation("session:abc-123-def")


def test_citation_decisions() -> bool:
    return validate_citation("decisions:d38ab304")


def test_citation_rejected_empty() -> bool:
    return not validate_citation("")


def test_citation_rejected_whitespace() -> bool:
    return not validate_citation("   ")


def test_citation_rejected_vague() -> bool:
    return not validate_citation("around line 50")


def test_citation_rejected_garbage() -> bool:
    return not validate_citation("see somewhere in the codebase")


def test_require_citation_raises() -> bool:
    try:
        require_citation("vague reference")
        return False
    except CitationError:
        return True


# ── Core memory ─────────────────────────────────────────────────────────────

def test_core_write_valid_citation() -> bool:
    store, tmp = _fresh_store()
    try:
        entry = store.write_core(
            pattern="Always backup files before theater kills",
            citation="project_homer.md:42",
            confidence="HIGH",
        )
        return entry.pattern.startswith("Always backup") and entry.confidence == "HIGH"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_write_rejects_empty_citation() -> bool:
    store, tmp = _fresh_store()
    try:
        try:
            store.write_core(pattern="test", citation="", confidence="MEDIUM")
            return False
        except (CitationError, ValueError):
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_write_rejects_invalid_citation() -> bool:
    store, tmp = _fresh_store()
    try:
        try:
            store.write_core(pattern="test", citation="somewhere", confidence="MEDIUM")
            return False
        except CitationError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_write_rejects_invalid_confidence() -> bool:
    store, tmp = _fresh_store()
    try:
        try:
            store.write_core(pattern="test", citation="f.py:1", confidence="WRONG")
            return False
        except ValueError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_write_rejects_empty_pattern() -> bool:
    store, tmp = _fresh_store()
    try:
        try:
            store.write_core(pattern="", citation="f.py:1", confidence="MEDIUM")
            return False
        except ValueError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_edit_requires_reason() -> bool:
    store, tmp = _fresh_store()
    try:
        entry = store.write_core(pattern="old", citation="a.py:1", confidence="HIGH")
        try:
            store.edit_core(key=entry.key, new_content="new", new_citation="a.py:2", reason="")
            return False
        except ValueError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_edit_requires_valid_citation() -> bool:
    store, tmp = _fresh_store()
    try:
        entry = store.write_core(pattern="old", citation="a.py:1", confidence="HIGH")
        try:
            store.edit_core(
                key=entry.key, new_content="new", new_citation="vague", reason="because",
            )
            return False
        except CitationError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_edit_valid() -> bool:
    store, tmp = _fresh_store()
    try:
        entry = store.write_core(pattern="old pattern", citation="a.py:1", confidence="MEDIUM")
        updated = store.edit_core(
            key=entry.key,
            new_content="new pattern",
            new_citation="a.py:2",
            reason="found clearer source",
        )
        return updated.pattern == "new pattern" and updated.citation == "a.py:2"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_edit_missing_key_raises() -> bool:
    store, tmp = _fresh_store()
    try:
        try:
            store.edit_core(
                key="nonexistent", new_content="x", new_citation="a:1", reason="r",
            )
            return False
        except KeyError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_roundtrip_read() -> bool:
    store, tmp = _fresh_store()
    try:
        store.write_core(pattern="entry 1", citation="a.py:1", confidence="HIGH")
        store.write_core(pattern="entry 2", citation="b.py:2", confidence="MEDIUM")
        entries = store.read_core()
        return len(entries) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_budget_status_shape() -> bool:
    store, tmp = _fresh_store()
    try:
        store.write_core(pattern="test", citation="f.py:1", confidence="MEDIUM")
        status = store.core.budget_status()
        return (
            "budget_tokens" in status
            and "used_tokens" in status
            and "headroom_tokens" in status
            and "over_budget" in status
            and "entry_count" in status
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_compaction_over_budget() -> bool:
    store, tmp = _fresh_store(budget=500)
    try:
        long_pattern = "x" * 1200  # ~300 tokens per entry
        for i in range(5):
            store.write_core(
                pattern=f"entry {i}: {long_pattern}",
                citation=f"f.py:{i + 1}",
                confidence="LOW" if i < 3 else "HIGH",
            )
        status_before = store.core.budget_status()
        result = store.compact_if_over_budget()
        status_after = store.core.budget_status()
        return (
            status_before["over_budget"]
            and result.get("compacted") is True
            and result["result"]["moved"] > 0
            and not status_after["over_budget"]
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_compaction_back_pointer_in_core() -> bool:
    store, tmp = _fresh_store(budget=500)
    try:
        long_pattern = "x" * 1200
        for i in range(5):
            store.write_core(
                pattern=f"entry {i}: {long_pattern}",
                citation=f"f.py:{i + 1}",
                confidence="LOW",
            )
        store.compact_if_over_budget()
        entries = store.read_core()
        has_back_pointer = any(e.get("archived_to") for e in entries)
        return has_back_pointer
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_core_compaction_high_confidence_preserved() -> bool:
    # Budget sized so 2 HIGH entries fit comfortably but 5 total exceed it.
    # Each long_pattern ~1200 chars + key/citation = ~312 tokens per entry.
    # 5 entries = ~1560 tokens. Budget 1000 -> compaction fires.
    # compact_target = 0.8 * 1000 = 800. 2 HIGH = 624 < 800 -> both kept in place.
    store, tmp = _fresh_store(budget=1000)
    try:
        long_pattern = "x" * 1200
        for i in range(3):
            store.write_core(pattern=f"LOW entry {i}: {long_pattern}", citation=f"a:{i + 1}", confidence="LOW")
        for i in range(2):
            store.write_core(pattern=f"HIGH entry {i}: {long_pattern}", citation=f"b:{i + 1}", confidence="HIGH")
        result = store.compact_if_over_budget()
        assert result.get("compacted") is True, "compaction should have fired"
        entries = store.read_core()
        # HIGH entries should NOT be back-pointers (should be kept in place)
        high_kept = [e for e in entries if "HIGH entry" in e["pattern"] and not e.get("archived_to")]
        return len(high_kept) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Recall memory ───────────────────────────────────────────────────────────

def test_recall_write_valid_citations() -> bool:
    store, tmp = _fresh_store()
    try:
        rid = store.write_recall(
            topic="the foo incident",
            content="we learned that foo must never be bar",
            citations=["project_homer.md:100", "a.py:42"],
        )
        return rid.startswith("recall_")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recall_write_rejects_no_citations() -> bool:
    store, tmp = _fresh_store()
    try:
        try:
            store.write_recall(topic="t", content="c", citations=[])
            return False
        except CitationError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recall_write_rejects_invalid_citation() -> bool:
    store, tmp = _fresh_store()
    try:
        try:
            store.write_recall(topic="t", content="c", citations=["vague"])
            return False
        except CitationError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recall_search_finds_match() -> bool:
    store, tmp = _fresh_store()
    try:
        store.write_recall(
            topic="brain pipeline",
            content="the classifier wires into UserPromptSubmit hook",
            citations=["brain_cli.py:200"],
        )
        results = store.search_recall("classifier", limit=5)
        return len(results) >= 1 and "classifier" in results[0]["content"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recall_search_empty_query() -> bool:
    store, tmp = _fresh_store()
    try:
        store.write_recall(topic="t", content="c", citations=["a:1"])
        return store.search_recall("") == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recall_count() -> bool:
    store, tmp = _fresh_store()
    try:
        assert store.recall.count() == 0
        store.write_recall(topic="x", content="y", citations=["a:1"])
        store.write_recall(topic="m", content="n", citations=["b:2"])
        return store.recall.count() == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Archival ────────────────────────────────────────────────────────────────

def test_archival_write_and_read() -> bool:
    store, tmp = _fresh_store()
    try:
        entry = CoreEntry(
            key="test_arch",
            pattern="archive me please",
            citation="a.py:1",
            confidence="HIGH",
        )
        archival_id = store.archival.write_from_core(entry)
        content = store.read_archival(archival_id)
        return content is not None and "archive me please" in content
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_archival_back_pointer_format() -> bool:
    store, tmp = _fresh_store()
    try:
        entry = CoreEntry(
            key="bp_test",
            pattern="content",
            citation="a.py:1",
            confidence="MEDIUM",
        )
        archival_id = store.archival.write_from_core(entry)
        content = store.read_archival(archival_id)
        return content is not None and f"mnemos:{archival_id}" in content
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Facade / health ─────────────────────────────────────────────────────────

def test_mnemos_health_shape() -> bool:
    store, tmp = _fresh_store()
    try:
        h = store.health()
        return (
            "core" in h
            and "recall_count" in h
            and "archival_count" in h
            and "fts_available" in h
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    # Citation validation
    ("citation_file_line", test_citation_file_line),
    ("citation_file_range", test_citation_file_range),
    ("citation_url", test_citation_url),
    ("citation_arxiv", test_citation_arxiv),
    ("citation_mnemos_back_pointer", test_citation_mnemos_back_pointer),
    ("citation_session", test_citation_session),
    ("citation_decisions", test_citation_decisions),
    ("citation_rejected_empty", test_citation_rejected_empty),
    ("citation_rejected_whitespace", test_citation_rejected_whitespace),
    ("citation_rejected_vague", test_citation_rejected_vague),
    ("citation_rejected_garbage", test_citation_rejected_garbage),
    ("require_citation_raises", test_require_citation_raises),
    # Core
    ("core_write_valid_citation", test_core_write_valid_citation),
    ("core_write_rejects_empty_citation", test_core_write_rejects_empty_citation),
    ("core_write_rejects_invalid_citation", test_core_write_rejects_invalid_citation),
    ("core_write_rejects_invalid_confidence", test_core_write_rejects_invalid_confidence),
    ("core_write_rejects_empty_pattern", test_core_write_rejects_empty_pattern),
    ("core_edit_requires_reason", test_core_edit_requires_reason),
    ("core_edit_requires_valid_citation", test_core_edit_requires_valid_citation),
    ("core_edit_valid", test_core_edit_valid),
    ("core_edit_missing_key_raises", test_core_edit_missing_key_raises),
    ("core_roundtrip_read", test_core_roundtrip_read),
    ("core_budget_status_shape", test_core_budget_status_shape),
    ("core_compaction_over_budget", test_core_compaction_over_budget),
    ("core_compaction_back_pointer_in_core", test_core_compaction_back_pointer_in_core),
    ("core_compaction_high_confidence_preserved", test_core_compaction_high_confidence_preserved),
    # Recall
    ("recall_write_valid_citations", test_recall_write_valid_citations),
    ("recall_write_rejects_no_citations", test_recall_write_rejects_no_citations),
    ("recall_write_rejects_invalid_citation", test_recall_write_rejects_invalid_citation),
    ("recall_search_finds_match", test_recall_search_finds_match),
    ("recall_search_empty_query", test_recall_search_empty_query),
    ("recall_count", test_recall_count),
    # Archival
    ("archival_write_and_read", test_archival_write_and_read),
    ("archival_back_pointer_format", test_archival_back_pointer_format),
    # Facade
    ("mnemos_health_shape", test_mnemos_health_shape),
]


def run_all() -> int:
    passed = 0
    failed = 0
    print("Homer MNEMOS smoke tests")
    print("=" * 56)
    for name, fn in TESTS:
        try:
            ok = fn()
            if ok:
                print(f"  {PASS} {name}")
                passed += 1
            else:
                print(f"  {FAIL} {name}")
                failed += 1
        except Exception as e:
            print(f"  {FAIL} {name}  --  {type(e).__name__}: {e}")
            failed += 1
    print("=" * 56)
    print(f"{passed}/{len(TESTS)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
