"""v0.23.3 安全修复回归测试 — 路径遍历 + delete fail-open (BE-079/083/092/098)"""

import json
import os
import tempfile
from pathlib import Path

import pytest


# =============================================================================
# _safe_filename
# =============================================================================


class TestSafeFilename:
    def test_normal_filename_unchanged(self):
        from vpbuddy.kb_api import _safe_filename
        assert _safe_filename("report.pdf") == "report.pdf"

    def test_strips_directory_prefix(self):
        from vpbuddy.kb_api import _safe_filename
        assert _safe_filename("../../../etc/passwd") == "passwd"

    def test_strips_absolute_path(self):
        from vpbuddy.kb_api import _safe_filename
        assert _safe_filename("/etc/shadow") == "shadow"

    def test_strips_windows_path(self):
        from vpbuddy.kb_api import _safe_filename
        assert _safe_filename("C:\\Windows\\System32\\evil.exe") == "evil.exe"

    def test_rejects_dot(self):
        from vpbuddy.kb_api import _safe_filename
        with pytest.raises(ValueError, match="非法文件名"):
            _safe_filename(".")

    def test_rejects_dotdot(self):
        from vpbuddy.kb_api import _safe_filename
        with pytest.raises(ValueError, match="非法文件名"):
            _safe_filename("..")

    def test_rejects_empty(self):
        from vpbuddy.kb_api import _safe_filename
        with pytest.raises(ValueError, match="非法文件名"):
            _safe_filename("")


# =============================================================================
# material_storage path traversal
# =============================================================================


class TestMaterialStoragePathTraversal:
    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="vpbuddy_test_mat_"))
        monkeypatch.setattr(
            "vpbuddy.server.material_storage._MATERIALS_BASE",
            self.tmpdir,
        )
        # 确保 _base() 不抛 RuntimeError
        monkeypatch.setattr(
            "vpbuddy.server.material_storage._MATERIALS_BASE",
            self.tmpdir,
            raising=False,
        )
        yield
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_store_file_rejects_path_traversal(self, monkeypatch):
        """BE-079: store_file 应剥离路径成分，只取纯文件名."""
        from vpbuddy.server.material_storage import store_file

        monkeypatch.setattr(
            "vpbuddy.server.material_storage._MATERIALS_BASE",
            self.tmpdir,
        )

        # 路径遍历被安全剥离：../../../etc/passwd → passwd
        meta = store_file("test_meeting", b"hello", "../../../etc/passwd", "text/plain")
        assert meta.filename == "passwd"
        assert meta.status == "stored"
        # 确认文件写在 mat_dir 内，未逃逸
        mat_dir = self.tmpdir / "test_meeting" / meta.material_id
        written = mat_dir / "passwd"
        assert written.exists()
        assert written.read_bytes() == b"hello"

    def test_store_file_accepts_normal_filename(self, monkeypatch):
        """正常文件名应正常写入."""
        from vpbuddy.server.material_storage import store_file

        monkeypatch.setattr(
            "vpbuddy.server.material_storage._MATERIALS_BASE",
            self.tmpdir,
        )

        meta = store_file("test_meeting", b"hello", "report.pdf", "application/pdf")
        assert meta.filename == "report.pdf"
        assert meta.status == "stored"

    def test_store_file_strips_path_components(self, monkeypatch):
        """store_file 应去除路径成分，只保留纯文件名."""
        from vpbuddy.server.material_storage import store_file

        monkeypatch.setattr(
            "vpbuddy.server.material_storage._MATERIALS_BASE",
            self.tmpdir,
        )

        meta = store_file("test_meeting", b"hello", "subdir/nested/report.pdf", "application/pdf")
        assert meta.filename == "report.pdf"

    def test_get_file_path_blocks_escape(self, monkeypatch):
        """BE-083: get_file_path 应拒绝解析到 mat_dir 外的路径."""
        from vpbuddy.server.material_storage import store_file, get_file_path

        monkeypatch.setattr(
            "vpbuddy.server.material_storage._MATERIALS_BASE",
            self.tmpdir,
        )

        # 正常写入一个文件
        meta = store_file("test_meeting", b"hello", "safe.txt", "text/plain")
        # 篡改 meta.json 写入恶意文件名
        mat_dir = self.tmpdir / "test_meeting" / meta.material_id
        meta_path = mat_dir / "meta.json"
        bad_meta = json.loads(meta_path.read_text())
        bad_meta["filename"] = "../../../etc/passwd"
        meta_path.write_text(json.dumps(bad_meta))

        # get_file_path 应返回 None（路径逃逸被拦截）
        result = get_file_path(meta.material_id)
        assert result is None


# =============================================================================
# KB delete fail-closed
# =============================================================================


class TestKBDeleteFailClosed:
    def test_delete_rejects_when_owner_check_throws(self, monkeypatch):
        """BE-098: owner 校验异常时应拒绝删除 (fail-closed)."""
        # 模拟 rag.get 抛异常
        class FakeRAG:
            def get(self, ids=None, **kwargs):
                raise RuntimeError("ChromaDB unavailable")
            def delete(self, ids):
                pass

        monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: FakeRAG())

        from vpbuddy.kb_api import handle_kb_delete
        result = handle_kb_delete("meeting123:abc", user_id="user_x")
        assert result["status"] == 500
        assert "cannot verify ownership" in result["error"]

    def test_delete_rejects_when_no_metadata(self, monkeypatch):
        """文档无 metadata 时应拒绝删除."""
        class FakeRAG:
            def get(self, ids=None, **kwargs):
                return {"ids": ["doc1"], "metadatas": [None]}
            def delete(self, ids):
                pass

        monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: FakeRAG())

        from vpbuddy.kb_api import handle_kb_delete
        result = handle_kb_delete("doc1", user_id="user_x")
        assert result["status"] == 404

    def test_delete_rejects_when_no_owner(self, monkeypatch):
        """文档无 owner 字段时应拒绝删除 (fail-closed)."""
        class FakeRAG:
            def get(self, ids=None, **kwargs):
                return {"ids": ["doc1"], "metadatas": [{"user_id": ""}]}
            def delete(self, ids):
                pass

        monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: FakeRAG())

        from vpbuddy.kb_api import handle_kb_delete
        result = handle_kb_delete("doc1", user_id="user_x")
        assert result["status"] == 403
        assert "ownership" in result["error"]

    def test_delete_allows_owner(self, monkeypatch):
        """owner 匹配时应允许删除."""
        delete_called = []

        class FakeRAG:
            def get(self, ids=None, **kwargs):
                return {"ids": ["doc1"], "metadatas": [{"user_id": "user_x"}]}
            def delete(self, ids):
                delete_called.append(ids)

        monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: FakeRAG())

        from vpbuddy.kb_api import handle_kb_delete
        result = handle_kb_delete("doc1", user_id="user_x")
        assert result["status"] == 200
        assert len(delete_called) == 1

    def test_delete_rejects_wrong_owner(self, monkeypatch):
        """不同 user 应拒绝删除."""
        class FakeRAG:
            def get(self, ids=None, **kwargs):
                return {"ids": ["doc1"], "metadatas": [{"user_id": "owner_x"}]}
            def delete(self, ids):
                pass

        monkeypatch.setattr("vpbuddy.kb_api.get_rag", lambda: FakeRAG())

        from vpbuddy.kb_api import handle_kb_delete
        result = handle_kb_delete("doc1", user_id="attacker_y")
        assert result["status"] == 403
        assert "denied" in result["error"].lower()
