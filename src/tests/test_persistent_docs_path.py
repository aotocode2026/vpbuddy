from __future__ import annotations

import importlib.util
from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / "vpbuddy" / "server" / "config.py"


def _load_config(monkeypatch, data_dir: Path, docs_dir: Path | None = None):
    monkeypatch.setenv("VPBUDDY_DATA_DIR", str(data_dir))
    if docs_dir is None:
        monkeypatch.delenv("VPBUDDY_DOCS_DIR", raising=False)
    else:
        monkeypatch.setenv("VPBUDDY_DOCS_DIR", str(docs_dir))
    spec = importlib.util.spec_from_file_location("vpbuddy_test_config", CONFIG)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_docs_default_follows_data_dir(monkeypatch, tmp_path: Path) -> None:
    config = _load_config(monkeypatch, tmp_path / "data")
    assert config.DOCS_DIR == tmp_path / "data" / "docs"


def test_explicit_docs_dir_remains_supported(monkeypatch, tmp_path: Path) -> None:
    config = _load_config(monkeypatch, tmp_path / "data", tmp_path / "custom-docs")
    assert config.DOCS_DIR == tmp_path / "custom-docs"


def test_doc_payload_and_deliverable_id_use_real_doc_paths(monkeypatch, tmp_path: Path) -> None:
    from vpbuddy import demo_version
    from vpbuddy.server import api_utils

    meeting_id = "meeting-with-hyphen"
    docs_dir = tmp_path / "docs"
    meeting_dir = docs_dir / meeting_id
    (meeting_dir / "demo").mkdir(parents=True)
    (meeting_dir / "req.md").write_text("# retained requirement", encoding="utf-8")
    (meeting_dir / "demo" / "demo.html").write_text("<html>retained demo</html>", encoding="utf-8")

    monkeypatch.setattr(api_utils, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(demo_version, "DOCS_DIR", docs_dir)

    req = api_utils._doc_payload(meeting_id, "req")
    demo = api_utils._doc_payload(meeting_id, "demo")
    assert req["status"] == "stored"
    assert req["content"] == "# retained requirement"
    assert demo["status"] == "stored"
    assert demo["doc_size"] > 0
    assert api_utils._doc_path(meeting_id, "demo") == meeting_dir / "demo" / "demo.html"
    assert api_utils._parse_deliverable_id(f"del-{meeting_id}-demo") == (meeting_id, "demo")
