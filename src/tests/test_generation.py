"""v0.23.2 regression tests — generation dedup + finalize idempotency.

Tests cover:
  - compute_input_hash: deterministic, excludes timestamps
  - mark_finalized / is_finalized: persistence + repeat detection
  - should_skip_generation: finalized / input_unchanged / ok
  - create_generation: idempotency key, unchanged detection
  - complete_generation: status update + output hash
  - GenerationRecord: to_dict / from_dict roundtrip
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure vpbuddy package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vpbuddy.generation import (
    GenerationRecord,
    complete_generation,
    compute_input_hash,
    create_generation,
    get_last_completed,
    is_finalized,
    is_stale_generation,
    load_generations,
    mark_finalized,
    should_skip_generation,
)


# ── Fixtures ──


@pytest.fixture
def tmp_data_dir():
    """Temporary data directory for isolated tests."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def meeting_id():
    return "test_meeting_001"


@pytest.fixture
def meeting_state(tmp_data_dir, meeting_id):
    """Create a minimal meeting state + cleanup."""
    state = {
        "meeting_id": meeting_id,
        "cleaned_text": "我们需要做一个用户登录功能",
        "requirements": [
            {"text": "用户登录", "priority": "high", "status": "pending", "id": "REQ-001"},
            {"text": "密码重置", "priority": "medium", "status": "pending", "id": "REQ-002"},
        ],
        "goals": [],
        "features": [],
        "risks": [],
        "open_questions": [],
        "last_updated": "2026-07-18T10:00:00Z",
    }
    state_path = tmp_data_dir / f"{meeting_id}.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    yield
    # Cleanup generation and finalized files
    for f in tmp_data_dir.glob(f"{meeting_id}.*"):
        try:
            f.unlink()
        except Exception:
            pass
    gen_dir = tmp_data_dir / "generations"
    if gen_dir.exists():
        for f in gen_dir.glob(f"{meeting_id}.*"):
            try:
                f.unlink()
            except Exception:
                pass


# ── compute_input_hash ──


def test_input_hash_deterministic(tmp_data_dir, meeting_id, meeting_state):
    """Same input → same hash."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 == h2
    assert len(h1) == 64  # sha256


def test_input_hash_changes_with_text(tmp_data_dir, meeting_id, meeting_state):
    """Changing cleaned_text changes hash."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)

    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cleaned_text"] = "我们需要做一个支付功能"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 != h2


def test_input_hash_ignores_last_updated(tmp_data_dir, meeting_id, meeting_state):
    """last_updated changes → same hash."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)

    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["last_updated"] = "2026-07-18T11:00:00Z"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 == h2


def test_input_hash_no_state_file(tmp_data_dir, meeting_id):
    """No state file → empty hash (but deterministic)."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 == h2
    assert len(h1) == 64


def test_input_hash_with_materials(tmp_data_dir, meeting_id, meeting_state):
    """Uploaded files affect hash."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)

    upload_dir = tmp_data_dir / "uploads" / meeting_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "spec.pdf").write_bytes(b"fake pdf content")

    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 != h2


def test_input_hash_with_chat(tmp_data_dir, meeting_id, meeting_state):
    """Chat messages affect hash."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)

    chat_path = tmp_data_dir / f"{meeting_id}.chat.json"
    chat_path.write_text(
        json.dumps([{"role": "user", "content": "请优化登录流程"}], ensure_ascii=False),
        encoding="utf-8",
    )

    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 != h2


# ── Finalize ──


def test_mark_finalized_creates_file(tmp_data_dir, meeting_id):
    """mark_finalized creates .finalized file."""
    assert not is_finalized(meeting_id, data_dir=tmp_data_dir)
    record = mark_finalized(meeting_id, data_dir=tmp_data_dir)
    assert record["is_repeat"] is False
    assert is_finalized(meeting_id, data_dir=tmp_data_dir)


def test_mark_finalized_repeat_detection(tmp_data_dir, meeting_id):
    """Second call to mark_finalized returns is_repeat=True."""
    mark_finalized(meeting_id, data_dir=tmp_data_dir)
    record = mark_finalized(meeting_id, data_dir=tmp_data_dir)
    assert record["is_repeat"] is True


def test_is_finalized_no_file(tmp_data_dir, meeting_id):
    """No .finalized file → False."""
    assert not is_finalized(meeting_id, data_dir=tmp_data_dir)


# ── should_skip_generation ──


def test_should_skip_finalized(tmp_data_dir, meeting_id, meeting_state):
    """Finalized meetings should skip generation."""
    mark_finalized(meeting_id, data_dir=tmp_data_dir)
    skip, reason, key = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is True
    assert reason == "finalized"


def test_should_skip_input_unchanged(tmp_data_dir, meeting_id, meeting_state):
    """Same input → skip (after first generation completed)."""
    # Create a completed generation first
    rec = create_generation(meeting_id, data_dir=tmp_data_dir)
    rec.status = "completed"
    complete_generation(meeting_id, rec.gen_id, status="completed", data_dir=tmp_data_dir)

    # Now check — same input should skip
    skip, reason, key = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is True
    assert reason == "input_unchanged"
    assert key is not None  # idempotency key returned


def test_should_not_skip_new_input(tmp_data_dir, meeting_id, meeting_state):
    """New input (no prior generation) → don't skip."""
    skip, reason, key = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is False
    assert reason == ""


def test_should_not_skip_changed_input(tmp_data_dir, meeting_id, meeting_state):
    """Input changed → don't skip."""
    # Create first completed generation
    rec = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec.gen_id, status="completed", data_dir=tmp_data_dir)

    # Change input
    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cleaned_text"] = "我们需要做一个全新的功能"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    skip, reason, key = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is False
    assert reason == ""


# ── create_generation ──


def test_create_generation_basic(tmp_data_dir, meeting_id, meeting_state):
    """create_generation creates a record with idempotency key."""
    rec = create_generation(meeting_id, data_dir=tmp_data_dir)
    assert rec.meeting_id == meeting_id
    assert rec.artifact == "docs"
    assert len(rec.input_hash) == 64
    assert rec.idempotency_key.startswith(f"{meeting_id}:docs:r")
    assert rec.gen_id >= 1
    assert rec.status == "queued"


def test_create_generation_unchanged(tmp_data_dir, meeting_id, meeting_state):
    """Same input after completed generation → status=unchanged."""
    rec1 = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec1.gen_id, status="completed", data_dir=tmp_data_dir)

    rec2 = create_generation(meeting_id, data_dir=tmp_data_dir)
    assert rec2.status == "unchanged"
    assert rec2.input_hash == rec1.input_hash


def test_create_generation_increments_gen_id(tmp_data_dir, meeting_id, meeting_state):
    """gen_id monotonically increases."""
    rec1 = create_generation(meeting_id, data_dir=tmp_data_dir)
    rec2 = create_generation(meeting_id, data_dir=tmp_data_dir)
    assert rec2.gen_id > rec1.gen_id


def test_create_generation_persisted(tmp_data_dir, meeting_id, meeting_state):
    """Generation records are persisted to disk."""
    rec = create_generation(meeting_id, data_dir=tmp_data_dir)
    records = load_generations(meeting_id, data_dir=tmp_data_dir)
    assert len(records) >= 1
    assert records[-1].gen_id == rec.gen_id
    assert records[-1].input_hash == rec.input_hash


# ── complete_generation ──


def test_complete_generation(tmp_data_dir, meeting_id, meeting_state):
    """complete_generation updates status and output hash."""
    rec = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(
        meeting_id,
        rec.gen_id,
        status="completed",
        output_hash="abc123",
        data_dir=tmp_data_dir,
    )
    last = get_last_completed(meeting_id, data_dir=tmp_data_dir)
    assert last is not None
    assert last.status == "completed"
    assert last.output_hash == "abc123"
    assert last.completed_at is not None


def test_complete_generation_failed(tmp_data_dir, meeting_id, meeting_state):
    """Failed generation is recorded."""
    rec = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec.gen_id, status="failed", data_dir=tmp_data_dir)
    last = get_last_completed(meeting_id, data_dir=tmp_data_dir)
    # failed generations are not "completed", so get_last_completed returns None
    assert last is None


# ── get_last_completed ──


def test_get_last_completed_none(tmp_data_dir, meeting_id):
    """No generations → None."""
    assert get_last_completed(meeting_id, data_dir=tmp_data_dir) is None


def test_get_last_completed_skips_failed(tmp_data_dir, meeting_id, meeting_state):
    """get_last_completed only returns completed records."""
    rec1 = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec1.gen_id, status="failed", data_dir=tmp_data_dir)

    rec2 = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec2.gen_id, status="completed", data_dir=tmp_data_dir)

    last = get_last_completed(meeting_id, data_dir=tmp_data_dir)
    assert last is not None
    assert last.gen_id == rec2.gen_id


# ── is_stale_generation ──


def test_is_stale_false_when_latest(tmp_data_dir, meeting_id, meeting_state):
    """Current generation is not stale when it's the latest completed."""
    rec1 = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec1.gen_id, status="completed", data_dir=tmp_data_dir)
    assert not is_stale_generation(meeting_id, rec1.gen_id, data_dir=tmp_data_dir)


def test_is_stale_true_when_newer_exists(tmp_data_dir, meeting_id, meeting_state):
    """Older generation is stale when newer completed exists."""
    rec1 = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec1.gen_id, status="completed", data_dir=tmp_data_dir)

    # Change input to force new generation
    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cleaned_text"] = "完全不同的话题"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    rec2 = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec2.gen_id, status="completed", data_dir=tmp_data_dir)

    assert is_stale_generation(meeting_id, rec1.gen_id, data_dir=tmp_data_dir)
    assert not is_stale_generation(meeting_id, rec2.gen_id, data_dir=tmp_data_dir)


# ── GenerationRecord roundtrip ──


def test_generation_record_roundtrip():
    """to_dict / from_dict roundtrip preserves all fields."""
    rec = GenerationRecord(
        gen_id=5,
        meeting_id="test",
        artifact="docs",
        input_hash="abc123",
        idempotency_key="test:docs:r1:abc",
        revision=1,
        status="completed",
        output_hash="def456",
        created_at=1234567890.0,
        completed_at=1234567900.0,
    )
    d = rec.to_dict()
    rec2 = GenerationRecord.from_dict(d, meeting_id="test")
    assert rec2.gen_id == rec.gen_id
    assert rec2.artifact == rec.artifact
    assert rec2.input_hash == rec.input_hash
    assert rec2.idempotency_key == rec.idempotency_key
    assert rec2.output_hash == rec.output_hash
    assert rec2.status == rec.status


# ── Integration: run_docs lifecycle simulation ──


def test_run_docs_flow_create_complete_skip(tmp_data_dir, meeting_id, meeting_state):
    """Simulate run_docs: create → completed → next call should skip."""
    rec1 = create_generation(meeting_id, data_dir=tmp_data_dir)
    assert rec1.status == "queued"

    complete_generation(meeting_id, rec1.gen_id, status="completed", data_dir=tmp_data_dir)

    # Same input → should skip
    skip, reason, key = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is True
    assert reason == "input_unchanged"
    assert key == rec1.idempotency_key


def test_run_docs_flow_first_then_changed(tmp_data_dir, meeting_id, meeting_state):
    """run_docs after input change should NOT skip."""
    rec1 = create_generation(meeting_id, data_dir=tmp_data_dir)
    complete_generation(meeting_id, rec1.gen_id, status="completed", data_dir=tmp_data_dir)

    # Change state
    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cleaned_text"] = "完全不同的话题内容"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    skip, reason, _ = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is False

    rec2 = create_generation(meeting_id, data_dir=tmp_data_dir)
    assert rec2.status == "queued"
    assert rec2.input_hash != rec1.input_hash
    assert rec2.revision == 2  # 1 completed → revision starts at 2


def test_should_skip_with_no_state_file(tmp_data_dir, meeting_id):
    """No state file at all → should NOT skip (first generation)."""
    skip, reason, _ = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is False


def test_finalized_blocks_all_generations(tmp_data_dir, meeting_id, meeting_state):
    """After mark_finalized, should_skip always returns finalized."""
    mark_finalized(meeting_id, data_dir=tmp_data_dir)

    # Before any generation
    skip, reason, _ = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip is True
    assert reason == "finalized"

    # Even after a generation is created (edge case)
    rec = create_generation(meeting_id, data_dir=tmp_data_dir)
    # But create_generation doesn't check finalized — that's task_manager's job
    # So test that should_skip still says finalized
    skip2, reason2, _ = should_skip_generation(meeting_id, data_dir=tmp_data_dir)
    assert skip2 is True
    assert reason2 == "finalized"


# ── demo_version publish lock ──


def test_demo_publish_lock_same_meeting():
    """_get_publish_lock returns the same lock object for the same meeting_id."""
    from vpbuddy.demo_version import _get_publish_lock

    lock1 = _get_publish_lock("meeting_A")
    lock2 = _get_publish_lock("meeting_A")
    lock3 = _get_publish_lock("meeting_B")

    assert lock1 is lock2
    assert lock1 is not lock3


def test_demo_publish_lock_threadsafe():
    """Multiple threads requesting the same meeting lock get the same object."""
    from vpbuddy.demo_version import _get_publish_lock
    import threading

    results = []

    def get_lock():
        results.append(id(_get_publish_lock("mtg_shared")))

    threads = [threading.Thread(target=get_lock) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 1


# ── Input hash: items with different status/priority ──


def test_input_hash_items_different_priority(tmp_data_dir, meeting_id, meeting_state):
    """Changing only item priority changes hash."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)

    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["requirements"][0]["priority"] = "low"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 != h2


def test_input_hash_items_different_status(tmp_data_dir, meeting_id, meeting_state):
    """Changing item status (pending → confirmed) changes hash."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)

    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["requirements"][0]["status"] = "confirmed"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 != h2


def test_input_hash_empty_items(tmp_data_dir, meeting_id, meeting_state):
    """Empty items list → deterministic hash."""
    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["requirements"] = []
    state["goals"] = []
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 == h2


def test_input_hash_shuffled_items_same(tmp_data_dir, meeting_id, meeting_state):
    """Items in different order produce same hash (sorted canonical)."""
    h1 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)

    state_path = tmp_data_dir / f"{meeting_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["requirements"].reverse()
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    h2 = compute_input_hash(meeting_id, data_dir=tmp_data_dir)
    assert h1 == h2
