from __future__ import annotations

from pathlib import Path

import pytest

from vpbuddy.rag_backend import _resolve_persist_dir


def test_missing_kb_dir_fails_without_creating_checkout_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VPBUDDY_KB_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="VPBUDDY_KB_DIR is required"):
        _resolve_persist_dir()
    assert not (tmp_path / "data" / "chroma").exists()


def test_configured_kb_dir_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb_dir = tmp_path / "kb"
    monkeypatch.setenv("VPBUDDY_KB_DIR", str(kb_dir))
    assert _resolve_persist_dir() == kb_dir


def test_relative_kb_dir_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPBUDDY_KB_DIR", "data/chroma")
    with pytest.raises(ValueError, match="must be absolute"):
        _resolve_persist_dir()


def test_explicit_absolute_path_remains_available_for_tests(tmp_path: Path) -> None:
    assert _resolve_persist_dir(tmp_path / "chroma") == tmp_path / "chroma"
