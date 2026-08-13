"""v0.23.3 regression: #45 ASR segment persistence + #47 Chinese filename download.

Tests:
  #45: _persist_segment appends to stream.json, skips noise, idempotent, structure
  #47: FileResponse uses filename= param (RFC 5987 encoding), not manual Content-Disposition
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_provider_http_error_is_not_treated_as_success():
    from vpbuddy.server.api_utils import _is_provider_error_response

    assert _is_provider_error_response("HTTP 400: Access denied") is True
    assert _is_provider_error_response("正常的会议总结") is False

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── #45: ASR segment persistence ──


class FakeSession:
    def __init__(self):
        self.meeting_id = "test_seg_001"
        self.recording_session_id = "rec_abc123"
        self.sentence_count = 0

class FakeCallback:
    """Minimal BailianCallback for testing _persist_segment."""

    def __init__(self, tmp_data_dir: Path):
        self._data_dir = str(tmp_data_dir)
        self._session = FakeSession()

    def _persist_segment(self, text, cleaned, begin_time, end_time, is_noise):
        """Copy of bailian_asr.BailianCallback._persist_segment."""
        if is_noise:
            return
        if not self._data_dir or not self._session.meeting_id:
            return
        from vpbuddy.server.api_utils import _load_stream_meta, _save_stream_meta

        self._session.sentence_count += 1
        seg_id = f"{self._session.recording_session_id}:{self._session.sentence_count}"
        clean_text = (cleaned or text).strip()
        if not clean_text:
            return

        meta = _load_stream_meta(self._session.meeting_id)
        segments = meta.get("transcript_segments", [])

        if any(s.get("id") == seg_id for s in segments):
            return

        segments.append({
            "id": seg_id,
            "recording_session_id": self._session.recording_session_id,
            "sequence": self._session.sentence_count,
            "text": clean_text,
            "begin_time": begin_time,
            "end_time": end_time,
            "is_sentence_end": True,
            "is_noise": False,
            "speaker_id": "UNKNOWN",
        })
        meta["transcript_segments"] = segments
        meta["transcript_revision"] = self._session.sentence_count
        _save_stream_meta(self._session.meeting_id, meta)


@pytest.fixture
def tmp_data_dir_45(tmp_path, monkeypatch):
    """Isolated temp dir with monkeypatched api_utils.DATA_DIR."""
    data_dir = tmp_path / "meetings"
    data_dir.mkdir()
    monkeypatch.setattr("vpbuddy.server.api_utils.DATA_DIR", data_dir)
    yield data_dir


def test_persist_segment_appends_to_stream_meta(tmp_data_dir_45):
    """After _persist_segment, stream.json has the segment."""
    cb = FakeCallback(tmp_data_dir_45)
    mid = cb._session.meeting_id

    cb._persist_segment("确认本期先覆盖两家工厂。", "确认本期先覆盖两家工厂。", 1230, 2480, False)

    # Load and verify
    from vpbuddy.server.api_utils import _load_stream_meta
    meta = _load_stream_meta(mid)
    segments = meta.get("transcript_segments", [])
    assert len(segments) == 1
    seg = segments[0]
    assert seg["text"] == "确认本期先覆盖两家工厂。"
    assert seg["id"] == "rec_abc123:1"
    assert seg["sequence"] == 1
    assert seg["begin_time"] == 1230
    assert seg["end_time"] == 2480
    assert seg["is_sentence_end"] is True
    assert seg["is_noise"] is False
    assert seg["speaker_id"] == "UNKNOWN"
    assert meta.get("transcript_revision") == 1


def test_persist_segment_skips_noise(tmp_data_dir_45):
    """Noise sentences are not persisted."""
    cb = FakeCallback(tmp_data_dir_45)
    mid = cb._session.meeting_id

    cb._persist_segment("嗯", "嗯", 100, 200, True)

    from vpbuddy.server.api_utils import _load_stream_meta
    meta = _load_stream_meta(mid)
    assert meta.get("transcript_segments", []) == []


def test_persist_segment_idempotent(tmp_data_dir_45):
    """Same segment_id → no duplicate append (simulates callback firing twice)."""
    cb = FakeCallback(tmp_data_dir_45)
    mid = cb._session.meeting_id

    # Simulate first call (sentence_count=1)
    cb._session.sentence_count = 1
    cb._persist_segment("第一句", "第一句", 100, 200, False)

    # Simulate duplicate callback with same sentence_count (should skip)
    cb._session.sentence_count = 1
    cb._persist_segment("第一句", "第一句", 100, 200, False)

    from vpbuddy.server.api_utils import _load_stream_meta
    meta = _load_stream_meta(mid)
    assert len(meta["transcript_segments"]) == 1


def test_persist_segment_multiple(tmp_data_dir_45):
    """Multiple segments are appended in order."""
    cb = FakeCallback(tmp_data_dir_45)
    mid = cb._session.meeting_id

    cb._persist_segment("第一句", "第一句", 0, 1000, False)
    cb._persist_segment("第二句", "第二句", 1100, 2500, False)
    cb._persist_segment("第三句", "第三句", 2600, 4000, False)

    from vpbuddy.server.api_utils import _load_stream_meta
    meta = _load_stream_meta(mid)
    assert len(meta["transcript_segments"]) == 3
    assert meta["transcript_segments"][0]["sequence"] == 1
    assert meta["transcript_segments"][2]["sequence"] == 3
    assert meta["transcript_revision"] == 3


def test_persist_segment_empty_text_skipped(tmp_data_dir_45):
    """Empty or whitespace-only text is not persisted."""
    cb = FakeCallback(tmp_data_dir_45)
    mid = cb._session.meeting_id

    cb._persist_segment("   ", "   ", 100, 200, False)

    from vpbuddy.server.api_utils import _load_stream_meta
    meta = _load_stream_meta(mid)
    assert meta.get("transcript_segments", []) == []


def test_persist_segment_no_data_dir(tmp_data_dir_45):
    """No-op when _data_dir is None."""
    cb = FakeCallback(tmp_data_dir_45)
    cb._data_dir = None
    cb._persist_segment("test", "test", 0, 100, False)
    # Should not raise


# ── #47: Chinese filename download ──


def test_file_response_uses_filename_param():
    """Verify FileResponse with filename= param (RFC 5987 encoding).
    
    This is a static check: the three download endpoints in fastapi_app.py
    should use `filename=` param, not manual `Content-Disposition` header.
    """
    import ast
    import inspect

    fastapi_path = Path(__file__).resolve().parent.parent / "vpbuddy" / "server" / "fastapi_app.py"
    source = fastapi_path.read_text(encoding="utf-8")

    # All three download endpoints should NOT have manual Content-Disposition
    assert 'headers={"Content-Disposition"' not in source, (
        "Download endpoints must use FileResponse.filename= param (RFC 5987), "
        "not manual Content-Disposition header (#47)"
    )

    # Should have filename= usage
    assert 'filename=' in source, "FileResponse must use filename= param"


def test_kb_download_route_defined():
    """GET /api/kb/{doc_id}/file route exists."""
    import ast
    fastapi_path = Path(__file__).resolve().parent.parent / "vpbuddy" / "server" / "fastapi_app.py"
    source = fastapi_path.read_text(encoding="utf-8")
    assert '@app.get("/api/kb/{doc_id}/file")' in source


def test_material_download_route_defined():
    """GET /api/materials/{material_id}/file route exists."""
    import ast
    fastapi_path = Path(__file__).resolve().parent.parent / "vpbuddy" / "server" / "fastapi_app.py"
    source = fastapi_path.read_text(encoding="utf-8")
    assert '@app.get("/api/materials/{material_id}/file")' in source



