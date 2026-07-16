"""v0.23.1 回归测试 — 事件驱动文档生成 + 状态写入 + chat上传不改KB + prompt纠正

覆盖:
- bailian_asr._write_state: 新会议自动创建 MeetingState
- bailian_asr.start_session: on_state_changed 回调传递
- bailian_asr.BailianCallback.on_event: sentence_end → on_state_changed 调用
- kb_api.handle_chat_upload: 文本/图片只落盘 , 不写 Chroma KB
- task_manager.get_task_manager: max_workers 默认 8
- task_manager.DocTaskManager: 并发安全
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# mock dashscope so bailian_asr tests work without real dashscope installed
_FAKE_DASHSCOPE = MagicMock()
_FAKE_DASHSCOPE.audio = MagicMock()
_FAKE_DASHSCOPE.audio.asr = MagicMock()
_FAKE_DASHSCOPE.audio.asr.Recognition = MagicMock()

class _FakeRecognitionResult:
    @staticmethod
    def is_sentence_end(sentence):
        return sentence.get("sentence_end", False) if isinstance(sentence, dict) else False

_FAKE_DASHSCOPE.audio.asr.RecognitionResult = _FakeRecognitionResult

sys.modules["dashscope"] = _FAKE_DASHSCOPE
sys.modules["dashscope.audio"] = MagicMock()
sys.modules["dashscope.audio.asr"] = _FAKE_DASHSCOPE.audio.asr

from vpbuddy.state import MeetingState
from vpbuddy.storage import MeetingStorage


class TestWriteStateAutoCreate:
    """v0.23.1: _write_state 在 meeting 不存在时自动创建 MeetingState."""

    def test_auto_creates_state_on_first_sentence(self, tmp_path):
        from vpbuddy.server.bailian_asr import _ASRSession, BailianCallback

        st = MeetingStorage(data_dir=tmp_path / "meetings")
        mid = "new_meeting_001"
        assert not st.exists(mid)

        loop = MagicMock()
        loop.call_soon_threadsafe = lambda fn, *args: fn(*args)
        loop.create_task = lambda c: None

        sess = _ASRSession(meeting_id=mid)
        sess.add_sentence("第一句测试。", "第一句测试。")

        cb = BailianCallback(loop, MagicMock(), sess, data_dir=str(st.data_dir))
        cb._write_state("第一句测试。", 1)

        assert st.exists(mid), "_write_state 应自动创建 MeetingState"
        loaded = st.load(mid)
        assert loaded.cleaned_text == "第一句测试。"

    def test_auto_creates_preserves_cleaned_text(self, tmp_path):
        from vpbuddy.server.bailian_asr import _ASRSession, BailianCallback

        st = MeetingStorage(data_dir=tmp_path / "meetings")
        mid = "auto_create_002"

        loop = MagicMock()
        loop.call_soon_threadsafe = lambda fn, *args: fn(*args)
        loop.create_task = lambda c: None

        sess = _ASRSession(meeting_id=mid)
        sess.add_sentence("A。", "A。")
        sess.add_sentence("B。", "B。")

        cb = BailianCallback(loop, MagicMock(), sess, data_dir=str(st.data_dir))
        cb._write_state("B。", 2)

        loaded = st.load(mid)
        assert loaded.cleaned_text == "A。B。"

    def test_auto_create_idempotent(self, tmp_path):
        from vpbuddy.server.bailian_asr import _ASRSession, BailianCallback

        st = MeetingStorage(data_dir=tmp_path / "meetings")
        mid = "idempotent_003"

        loop = MagicMock()
        loop.call_soon_threadsafe = lambda fn, *args: fn(*args)
        loop.create_task = lambda c: None

        sess = _ASRSession(meeting_id=mid)
        sess.add_sentence("1。", "1。")
        cb = BailianCallback(loop, MagicMock(), sess, data_dir=str(st.data_dir))
        cb._write_state("1。", 1)
        assert st.exists(mid)

        mtime_1 = (st.data_dir / f"{mid}.json").stat().st_mtime

        sess.add_sentence("2。", "2。")
        cb._write_state("2。", 2)
        mtime_2 = (st.data_dir / f"{mid}.json").stat().st_mtime

        assert st.exists(mid)
        assert st.load(mid).cleaned_text == "1。2。"
        assert mtime_2 >= mtime_1

    def test_no_data_dir_does_nothing(self):
        from vpbuddy.server.bailian_asr import _ASRSession, BailianCallback

        loop = MagicMock()
        loop.create_task = lambda c: None
        sess = _ASRSession(meeting_id="nodir_004")
        sess.add_sentence("t。", "t。")
        cb = BailianCallback(loop, MagicMock(), sess, data_dir="")
        cb._write_state("t。", 1)
        assert sess.accumulated_text == "t。"


class TestOnStateChangedCallback:
    """v0.23.1: on_state_changed 句完成→task_manager.submit."""

    def test_callback_registered_in_start_session(self, tmp_path):
        from vpbuddy.server.bailian_asr import start_session

        called: list[str] = []

        def on_changed(mid: str):
            called.append(mid)

        loop = MagicMock()
        loop.create_task = lambda c: None
        loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None

        sess = start_session(
            loop=loop,
            meeting_id="cb_test_001",
            send_json=lambda msg: None,
            data_dir=str(tmp_path),
            on_state_changed=on_changed,
        )
        assert sess is not None
        assert sess.meeting_id == "cb_test_001"

    def _make_sentence_result(self, text, sentence_end=True, sentence_id=1):
        result = MagicMock()
        result.get_sentence.return_value = {
            "text": text,
            "sentence_end": sentence_end,
            "sentence_id": sentence_id,
            "begin_time": 0,
            "end_time": 1000,
        }
        return result

    def test_on_event_calls_on_state_changed(self, tmp_path):
        from vpbuddy.server.bailian_asr import _ASRSession, BailianCallback

        called: list[str] = []

        def on_changed(mid: str):
            called.append(mid)

        st = MeetingStorage(data_dir=tmp_path / "meetings")
        loop = MagicMock()
        loop.create_task = lambda c: None

        def _cs_threadsafe(fn, *args):
            fn(*args) if callable(fn) else None

        loop.call_soon_threadsafe = _cs_threadsafe

        sess = _ASRSession(meeting_id="oe_test_002")
        sess.add_sentence("测试内容。", "测试内容。")
        send = MagicMock()
        cb = BailianCallback(loop, send, sess, data_dir=str(st.data_dir), on_state_changed=on_changed)

        result = self._make_sentence_result("测试内容。")
        cb.on_event(result)

        assert "oe_test_002" in called, "sentence_end 时应触发 on_state_changed"
        assert st.exists("oe_test_002"), "sentence_end 时也写 state"

    def test_on_event_empty_sentence_no_callback(self, tmp_path):
        from vpbuddy.server.bailian_asr import _ASRSession, BailianCallback

        called: list[str] = []

        def on_changed(mid: str):
            called.append(mid)

        st = MeetingStorage(data_dir=tmp_path / "meetings")
        loop = MagicMock()
        loop.create_task = lambda c: None
        loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None

        sess = _ASRSession(meeting_id="empty_003")
        send = MagicMock()
        cb = BailianCallback(loop, send, sess, data_dir=str(st.data_dir), on_state_changed=on_changed)

        result = self._make_sentence_result("   ")
        cb.on_event(result)

        assert "empty_003" not in called, "空文本不应触发 on_state_changed"

    def test_on_event_no_callback_set_does_not_crash(self, tmp_path):
        from vpbuddy.server.bailian_asr import _ASRSession, BailianCallback

        st = MeetingStorage(data_dir=tmp_path / "meetings")
        loop = MagicMock()
        loop.create_task = lambda c: None
        loop.call_soon_threadsafe = lambda fn, *a: fn(*a) if callable(fn) else None

        sess = _ASRSession(meeting_id="no_cb_004")
        sess.add_sentence("内容。", "内容。")
        send = MagicMock()
        cb = BailianCallback(loop, send, sess, data_dir=str(st.data_dir), on_state_changed=None)

        result = self._make_sentence_result("内容。")
        cb.on_event(result)

        assert st.exists("no_cb_004")


class TestChatUploadNoKB:
    """v0.23.1: handle_chat_upload 只落盘 uploads/，不写 Chroma KB."""

    def test_text_file_not_in_kb(self, tmp_path, monkeypatch):
        monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")

        from vpbuddy.kb_api import handle_chat_upload

        boundary = b"----testboundary123"
        text_field = "分析这个文档".encode("utf-8")
        file_content = "这是测试文档内容。".encode("utf-8")
        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="text"\r\n\r\n'
            + text_field + b"\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="files"; filename="test.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
            + file_content + b"\r\n"
            b"--" + boundary + b"--\r\n"
        )
        ct = f"multipart/form-data; boundary={boundary.decode()}"

        result = handle_chat_upload(body, ct, "chat_no_kb_001", user_id="u1")

        assert result["status"] == 200
        files = result["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "test.txt"
        assert files[0]["status"] == "stored", (
            f"期望 'stored' (只落盘)，实际 {files[0].get('status')}"
        )
        assert "path" in files[0]
        assert Path(files[0]["path"]).exists()

    def test_image_not_in_kb(self, tmp_path, monkeypatch):
        monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")

        from vpbuddy.kb_api import handle_chat_upload

        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
            b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        boundary = b"----imgboundary456"
        text_en = "what is this image".encode("utf-8")
        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="text"\r\n\r\n'
            + text_en + b"\r\n"
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="files"; filename="test.png"\r\n'
            b"Content-Type: image/png\r\n\r\n"
            + png_data + b"\r\n"
            b"--" + boundary + b"--\r\n"
        )
        ct = f"multipart/form-data; boundary={boundary.decode()}"

        with patch("vpbuddy.kb_api._image_to_b64_data_uri", return_value="data:image/png;base64,FAKE=="):
            result = handle_chat_upload(body, ct, "chat_img_002", user_id="u1")

        assert result["status"] == 200
        files = result["files"]
        assert len(files) == 1
        assert files[0]["status"] == "image", (
            f"期望 'image' (图片不入 KB)，实际 {files[0].get('status')}"
        )

    def test_kb_doc_ids_always_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("vpbuddy.kb_api.UPLOADS_DIR", tmp_path / "uploads")

        from vpbuddy.kb_api import handle_chat_upload

        boundary = b"----emptykb789"
        md_content = "# Markdown doc content".encode("utf-8")
        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="files"; filename="doc.md"\r\n'
            b"Content-Type: text/markdown\r\n\r\n"
            + md_content + b"\r\n"
            b"--" + boundary + b"--\r\n"
        )
        ct = f"multipart/form-data; boundary={boundary.decode()}"

        result = handle_chat_upload(body, ct, "no_kb_ids_003", user_id="u1")
        assert result["kb_doc_ids"] == [], "chat 上传不应再有 kb_doc_ids"


class TestTaskManagerDefaults:
    """v0.23.1: max_workers 默认值改为 8."""

    def test_default_max_workers_is_8(self):
        from vpbuddy.task_manager import DocTaskManager

        mgr = DocTaskManager()
        assert mgr.executor._max_workers == 8, (
            f"期望 max_workers=8，实际 {mgr.executor._max_workers}"
        )

    def test_get_task_manager_uses_default_8(self):
        import vpbuddy.task_manager as tm
        tm._doc_task_manager = None
        mgr = tm.get_task_manager()
        assert mgr.executor._max_workers == 8

    def test_get_task_manager_custom_workers(self):
        import vpbuddy.task_manager as tm
        tm._doc_task_manager = None
        mgr = tm.get_task_manager(max_workers=12)
        assert mgr.executor._max_workers == 12
        tm._doc_task_manager = None


class TestDocTaskManagerExtended:
    """v0.23.1: 并发安全 & cleanup 回归."""

    def test_concurrent_meetings_not_blocked(self):
        from vpbuddy.task_manager import DocTaskManager

        mgr = DocTaskManager(max_workers=8)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        t1 = mgr.submit("mtg_a", lambda gid, mid: {"ok": True})
        t2 = mgr.submit("mtg_b", lambda gid, mid: {"ok": True})
        t3 = mgr.submit("mtg_c", lambda gid, mid: {"ok": True})

        assert t1 is not None
        assert t2 is not None
        assert t3 is not None
        assert "mtg_a" in mgr._queues
        assert "mtg_b" in mgr._queues
        assert "mtg_c" in mgr._queues

    def test_same_meeting_sequential_completes(self):
        from vpbuddy.task_manager import DocTaskManager, DocTaskStatus

        mgr = DocTaskManager(max_workers=8)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        t1 = mgr.submit("seq_mtg", lambda gid, mid: {"n": 1})
        t1.status = DocTaskStatus.COMPLETED

        t2 = mgr.submit("seq_mtg", lambda gid, mid: {"n": 2})
        assert t2 is not None, "completed 后新 submit 应成功"

    def test_submit_respects_meeting_isolation(self):
        from vpbuddy.task_manager import DocTaskManager, DocTaskStatus

        mgr = DocTaskManager(max_workers=8)
        mgr.executor.submit = lambda fn, *a, **kw: MagicMock()

        mgr.submit("iso_a", lambda gid, mid: {"a": 1})
        t2 = mgr.submit("iso_b", lambda gid, mid: {"b": 2})

        assert t2 is not None
        assert mgr._queues["iso_a"].current_task is not None
        assert mgr._queues["iso_b"].current_task is not None


class TestServerHealthzAndStartup:
    """v0.23.1: 服务器健康检查."""

    def test_healthz_endpoint_exists(self):
        try:
            from vpbuddy.server.fastapi_app import app
            from fastapi.testclient import TestClient
            client = TestClient(app)
            resp = client.get("/healthz")
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
        except ImportError:
            pytest.skip("fastapi.testclient 不可用")

    def test_meeting_stream_start_endpoint(self):
        try:
            from vpbuddy.server.fastapi_app import app
            from fastapi.testclient import TestClient
            client = TestClient(app)
            resp = client.post("/api/meetings/stream_start", json={
                "meeting_id": "healthz_test_001",
                "audio_source": "microphone",
            })
            assert resp.status_code in (200, 401, 422, 405)
        except ImportError:
            pytest.skip("fastapi.testclient 不可用")


class TestFormatStateSummary:
    """v0.23.1: format_state_summary 包含上传文件 + chat 历史."""

    def test_uploaded_files_listed(self, tmp_path):
        from vpbuddy.sub_session_controller import format_state_summary, DATA_DIR as ORIG_DATA_DIR
        import vpbuddy.sub_session_controller as ssc

        monkeypatch_dir = tmp_path / "data"
        upload_dir = monkeypatch_dir / "uploads" / "fmt_test_001"
        upload_dir.mkdir(parents=True, exist_ok=True)
        (upload_dir / "设计稿.png").write_bytes(b"fake png")
        (upload_dir / "需求文档.md").write_bytes("# 需求".encode("utf-8"))
        (upload_dir / "合同.pdf").write_bytes(b"%PDF-1.4")

        ssc.DATA_DIR = monkeypatch_dir
        try:
            state = MeetingState(meeting_id="fmt_test_001", platform="local")
            state.cleaned_text = "做个小程序"

            summary = format_state_summary(state)
            assert "设计稿.png" in summary
            assert "需求文档.md" in summary
            assert "合同.pdf" in summary
        finally:
            ssc.DATA_DIR = ORIG_DATA_DIR

    def test_empty_uploads_safe(self, tmp_path):
        from vpbuddy.sub_session_controller import format_state_summary, DATA_DIR as ORIG_DATA_DIR
        import vpbuddy.sub_session_controller as ssc

        monkeypatch_dir = tmp_path / "data"
        ssc.DATA_DIR = monkeypatch_dir
        try:
            state = MeetingState(meeting_id="no_uploads", platform="local")
            state.cleaned_text = "测试"
            summary = format_state_summary(state)
            assert "测试" in summary
        finally:
            ssc.DATA_DIR = ORIG_DATA_DIR


class TestPromptContent:
    """v0.23.1: prompt 纠正 — batch_docs.md 说 read_file 不进 KB."""

    def test_batch_docs_prompt_no_kb_misleading(self):
        from pathlib import Path as _Path
        prompt_path = _Path(__file__).parent.parent / "vpbuddy" / "prompts" / "batch_docs.md"
        content = prompt_path.read_text(encoding="utf-8")

        assert "知识库（KB）" not in content, (
            "batch_docs.md 不应再提 KB 入库——chat 上传不入 KB"
        )
        assert "read_file" in content, (
            "batch_docs.md 应引导 agent 用 read_file 读上传文件"
        )

    def test_vp_chat_prompt_no_6_sub_agents(self):
        import inspect
        from vpbuddy.server.api_utils import _get_chat_agent

        source = inspect.getsource(_get_chat_agent)
        assert "6 个子 agent" not in source, (
            "VP Chat system prompt 不应再说 '6 个子 agent' — 实际只有 2 个"
        )
        assert "2 个自动子 agent" in source, (
            "VP Chat system prompt 应明确 2 个自动子 agent"
        )
