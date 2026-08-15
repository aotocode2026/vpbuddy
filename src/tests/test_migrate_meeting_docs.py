from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_meeting_docs.py"
SPEC = importlib.util.spec_from_file_location("migrate_meeting_docs", SCRIPT)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_merge_only_recognized_meetings_and_preserves_sources(tmp_path: Path) -> None:
    data = tmp_path / "data"
    rollback = tmp_path / "rollback-docs"
    active = tmp_path / "active-docs"
    target = data / "docs"
    data.mkdir()
    (data / "m1.json").write_text(json.dumps({"meeting_id": "m1"}), encoding="utf-8")
    (data / "auth.json").write_text(json.dumps({"users": []}), encoding="utf-8")
    (rollback / "m1" / "demo").mkdir(parents=True)
    (rollback / "m1" / "req.md").write_text("old req", encoding="utf-8")
    (rollback / "m1" / "demo" / "demo.html").write_text("old demo", encoding="utf-8")
    (rollback / "test_fixture").mkdir(parents=True)
    (rollback / "test_fixture" / "req.md").write_text("not user data", encoding="utf-8")
    (active / "m1").mkdir(parents=True)
    (active / "m1" / "arch.md").write_text("new arch", encoding="utf-8")

    ids = MIGRATION.meeting_ids(data)
    dry_run = MIGRATION.merge([rollback, active], target, ids, False)
    assert dry_run == {"meetings": 1, "copied": 3, "identical": 0, "conflicts": 0}
    assert not target.exists()

    applied = MIGRATION.merge([rollback, active], target, ids, True)
    assert applied["copied"] == 3
    assert (target / "m1" / "req.md").read_text(encoding="utf-8") == "old req"
    assert (target / "m1" / "arch.md").read_text(encoding="utf-8") == "new arch"
    assert not (target / "test_fixture").exists()
    assert (rollback / "m1" / "req.md").exists()


def test_merge_aborts_before_copy_when_contents_conflict(tmp_path: Path) -> None:
    source_a = tmp_path / "a"
    source_b = tmp_path / "b"
    target = tmp_path / "target"
    for source, value in ((source_a, "a"), (source_b, "b")):
        (source / "m1").mkdir(parents=True)
        (source / "m1" / "req.md").write_text(value, encoding="utf-8")

    result = MIGRATION.merge([source_a, source_b], target, {"m1"}, True)
    assert result["conflicts"] == 1
    assert not target.exists()
