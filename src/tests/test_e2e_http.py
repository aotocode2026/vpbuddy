"""VPBuddy HTTP E2E 测试 (2026-07-18 重写)

纯 HTTP 测试, 从本地调用远程服务端 API, 不 import vpbuddy 内部模块。
取代旧的 test_e2e_integration.py (in-process, 需 GPU/本地 server)。

触发方式:
    RUN_E2E=1 pytest src/tests/test_e2e_http.py -v

目标服务器: VP_E2E_GPU_URL 环境变量, 默认 http://47.100.182.3:28765
"""
import asyncio
import json
import os
import time
import uuid
import urllib.request
import urllib.error
import urllib.parse

import pytest
import websockets

# === Gate ===
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="需 RUN_E2E=1 显式触发",
)

GPU = os.environ.get("VP_E2E_GPU_URL", "http://47.100.182.3:28765")


# === helpers ===

def _api(path: str, method: str = "GET", body: bytes = None, token: str = "",
         ct: str = "application/json", timeout: int = 30):
    """统一 HTTP 调用, 返回 (status_code, parsed_json)."""
    h = {"Content-Type": ct}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{GPU}{path}", data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = b""
        try:
            raw = e.read()
        except Exception:
            pass
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw.decode(errors="replace")[:500]}


def _register(email: str = None, password: str = "t123456", max_retries: int = 5):
    """注册新用户 (带重试, 应对 429 限流), 返回 (token, user_id, email)."""
    e = email or f"e2e_{uuid.uuid4().hex[:8]}@test.com"
    last_err = None
    for attempt in range(max_retries):
        c, r = _api("/api/auth/register", "POST",
                     json.dumps({"email": e, "password": password}).encode())
        if c == 200:
            return r["token"], r["user_id"], e
        if c == 429:
            wait = 2 ** attempt
            time.sleep(wait)
            last_err = (c, r)
            continue
        last_err = (c, r)
        break
    raise AssertionError(f"register failed after {max_retries} retries: {last_err}")


def _create_meeting(token: str, meeting_id: str = None, project_name: str = None):
    """创建会议, 返回 meeting_id."""
    mid = meeting_id or f"E2E_{uuid.uuid4().hex[:8]}"
    params = f"meeting_id={mid}&audio_source=microphone"
    if project_name:
        params += f"&project_name={urllib.parse.quote(project_name)}"
    c, r = _api(f"/api/meetings/stream_start?{params}", "POST", token=token)
    assert c == 200, f"stream_start failed ({c}): {r}"
    assert r.get("meeting_id") == mid
    return mid


# === session-scoped fixtures (避免 429 限流) ===

@pytest.fixture(scope="session")
def shared_token():
    """会话级共享 token, 减少注册次数."""
    token, uid, email = _register()
    return token


@pytest.fixture(scope="session")
def shared_meeting(shared_token):
    """会话级共享会议, 用于只读测试."""
    mid = _create_meeting(shared_token)
    yield mid
    _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)


# === Tests ===

class TestHealth:
    """公开端点, 无需认证."""

    def test_healthz(self):
        c, r = _api("/healthz", timeout=5)
        assert c == 200
        assert r.get("ok") is True

    def test_openapi_schema(self):
        c, r = _api("/openapi.json", timeout=5)
        assert c == 200
        assert "openapi" in r
        assert "paths" in r
        paths = r["paths"]
        assert "/api/auth/register" in paths
        assert "/api/auth/login" in paths
        assert "/api/meetings" in paths


class TestAuth:
    """认证流程."""

    def test_register_returns_token(self):
        token, uid, email = _register()
        assert token is not None
        assert len(token) > 20
        assert uid is not None
        assert "@" in email

    def test_register_rejects_invalid_email(self):
        c, r = _api("/api/auth/register", "POST",
                     json.dumps({"email": "not-an-email", "password": "t123456"}).encode())
        assert c == 400

    def test_login_existing_user(self):
        email = f"e2el_{uuid.uuid4().hex[:8]}@test.com"
        _register(email=email)
        c, r = _api("/api/auth/login", "POST",
                     json.dumps({"email": email, "password": "t123456"}).encode())
        assert c == 200
        assert r.get("token") is not None

    def test_login_wrong_password(self):
        email = f"e2elw_{uuid.uuid4().hex[:8]}@test.com"
        _register(email=email)
        c, r = _api("/api/auth/login", "POST",
                     json.dumps({"email": email, "password": "wrong"}).encode())
        assert c in (401, 400, 403)

    def test_auth_me_returns_user(self, shared_token):
        c, r = _api("/api/auth/me", token=shared_token)
        assert c == 200
        assert r.get("user_id") is not None
        assert r.get("email") is not None

    def test_auth_me_rejects_bad_token(self):
        c, r = _api("/api/auth/me", token="bad-token-12345")
        assert c in (401, 403, 429), f"unexpected {c}"

    def test_protected_endpoint_without_auth(self):
        c, r = _api("/api/meetings")
        assert c in (401, 403, 429), f"unexpected {c}"


class TestMeetingLifecycle:
    """会议生命周期: 创建 → 查询 → 关闭 → 删除."""

    def test_create_meeting_with_custom_id(self, shared_token):
        mid = f"E2E_ML_{uuid.uuid4().hex[:8]}"
        _create_meeting(shared_token, meeting_id=mid)
        c, r = _api(f"/api/meetings/{mid}", token=shared_token)
        assert c == 200
        assert r.get("id") == mid
        _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)

    def test_create_meeting_auto_id(self, shared_token):
        mid = _create_meeting(shared_token)
        assert mid.startswith("E2E_")
        _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)

    def test_create_meeting_with_project_name(self, shared_token):
        mid = _create_meeting(shared_token, project_name="HTTP E2E Test Meeting")
        c, r = _api(f"/api/meetings/{mid}", token=shared_token)
        assert c == 200
        _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)

    def test_meeting_list(self, shared_token):
        c, r = _api("/api/meetings", token=shared_token)
        assert c == 200
        assert "meetings" in r
        assert "count" in r
        assert isinstance(r["meetings"], list)

    def test_meeting_detail_includes_expected_fields(self, shared_meeting, shared_token):
        c, r = _api(f"/api/meetings/{shared_meeting}", token=shared_token)
        assert c == 200
        for field in ["id", "state", "docs"]:
            assert field in r, f"missing field: {field}"

    def test_close_meeting(self, shared_token):
        mid = _create_meeting(shared_token)
        c, r = _api(f"/api/meetings/{mid}/close", "POST", token=shared_token)
        assert c == 200, f"close failed ({c}): {r}"
        _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)

    def test_delete_meeting(self, shared_token):
        mid = _create_meeting(shared_token)
        c, r = _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)
        assert c == 200
        c2, _ = _api(f"/api/meetings/{mid}", token=shared_token)
        assert c2 == 404

    def test_get_nonexistent_meeting(self, shared_token):
        c, r = _api(f"/api/meetings/E2E_NONEXIST_{uuid.uuid4().hex[:8]}", token=shared_token)
        assert c == 404

    def test_meeting_isolation(self, shared_token):
        """两个用户的 meeting list 互不可见."""
        token_b, _, _ = _register()
        mid_a = _create_meeting(shared_token)
        try:
            c, r = _api("/api/meetings", token=shared_token)
            ids_a = [m.get("meeting_id") for m in r["meetings"]]
            assert mid_a in ids_a, f"{mid_a} not in {ids_a}"

            c, r = _api("/api/meetings", token=token_b)
            ids_b = [m.get("meeting_id") for m in r["meetings"]]
            assert mid_a not in ids_b, f"meeting leak: {mid_a} visible to another user"
        finally:
            _api(f"/api/meetings/{mid_a}", "DELETE", token=shared_token)


class TestChat:
    """会议聊天."""

    def test_chat_simple_message(self, shared_token):
        mid = _create_meeting(shared_token)
        body = json.dumps({"message": "你好, 帮我总结一下"}).encode()
        c, r = _api(f"/api/meetings/{mid}/chat", "POST", body=body, token=shared_token, timeout=60)
        assert c in (200, 202), f"chat failed ({c}): {r}"
        _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)

    def test_chat_empty_message(self, shared_token):
        mid = _create_meeting(shared_token)
        body = json.dumps({"message": ""}).encode()
        c, r = _api(f"/api/meetings/{mid}/chat", "POST", body=body, token=shared_token, timeout=30)
        assert c in (200, 202, 400, 422), f"unexpected {c}: {r}"
        _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)


class TestKB:
    """知识库端点."""

    def test_kb_list(self, shared_token):
        c, r = _api("/api/kb/list", token=shared_token)
        assert c == 200
        assert "docs" in r

    def test_kb_search(self, shared_token):
        q = urllib.parse.quote("测试")
        c, r = _api(f"/api/kb/search?q={q}&top_k=3", token=shared_token)
        assert c == 200
        assert "results" in r


class TestDeliverables:
    """会议产出物."""

    def test_deliverables_list(self, shared_meeting, shared_token):
        c, r = _api(f"/api/meetings/{shared_meeting}/deliverables", token=shared_token)
        # 新会议无产出物, 404 也可接受
        assert c in (200, 404), f"unexpected status {c}"

    def test_doc_status(self, shared_meeting, shared_token):
        c, r = _api(f"/api/meetings/{shared_meeting}/docs/req", token=shared_token)
        assert c == 200
        assert r.get("kind") == "req"
        assert r.get("status") in ("pending", "stored", "generating")


class TestErrorHandling:
    """错误处理."""

    def test_invalid_json_body(self):
        c, r = _api("/api/auth/register", "POST",
                     body=b"not json", ct="application/json")
        assert c >= 400, f"expected 4xx/5xx, got {c}: {r}"

    def test_wrong_content_type(self):
        c, r = _api("/api/auth/register", "POST",
                     body=b"email=test@test.com&password=123", ct="text/plain")
        assert c >= 400, f"expected 4xx/5xx, got {c}: {r}"

    def test_method_not_allowed(self):
        c, r = _api("/api/auth/me", "POST", token="fake")
        assert c >= 400, f"expected 4xx/5xx, got {c}: {r}"


class TestConcurrent:
    """并发/多次操作."""

    def test_create_many_meetings(self, shared_token):
        ids = []
        try:
            for _ in range(3):
                mid = _create_meeting(shared_token)
                ids.append(mid)
            c, r = _api("/api/meetings", token=shared_token)
            found = [m.get("meeting_id") for m in r["meetings"]]
            for mid in ids:
                assert mid in found, f"{mid} not in {found}"
        finally:
            for mid in ids:
                _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)


class TestWebSocketASR:
    """WebSocket 实时 ASR 测试 (覆盖 bailian_asr 链路)."""

    async def _ws_asr_roundtrip(self, token: str):
        """WebSocket ASR 完整链路: 连接 → 发音频 → 收转写/错误 → 关闭."""
        mid = _create_meeting(token)
        try:
            ws_url = GPU.replace("http://", "ws://").rstrip("/")
            url = f"{ws_url}/api/meetings/{mid}/realtime_asr?token={urllib.parse.quote(token)}"
            errors = []

            async with websockets.connect(url, max_size=2**24) as ws:
                # 发送启动消息
                await ws.send(json.dumps({
                    "type": "start",
                    "format": "pcm",
                    "sample_rate": 16000,
                }))

                # 发送 5 帧静音 PCM (16kHz mono 16bit, 100ms = 3200 bytes)
                silence = b'\x00' * 3200
                for _ in range(5):
                    await ws.send(silence)
                    await asyncio.sleep(0.1)

                # 收消息 5 秒, 检查有没有 error
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1)
                        data = json.loads(msg)
                        typ = data.get("type", "")
                        if typ in ("asr_error", "error"):
                            errors.append(data.get("error", str(data)))
                        elif typ == "transcript":
                            pass  # 静音不会有转写, 正常
                    except asyncio.TimeoutError:
                        continue

                # 发送停止
                await ws.send(json.dumps({"type": "stop"}))

            return mid, errors
        except Exception:
            _api(f"/api/meetings/{mid}", "DELETE", token=token)
            raise

    def test_ws_asr_no_crash(self, shared_token):
        """连接 WebSocket ASR, 发静音, 验证不崩且无 error 返回."""
        mid, errors = asyncio.run(self._ws_asr_roundtrip(shared_token))
        try:
            # 关键: error callback 线程不应该崩 (之前 RecognitionResult.__str__ bug)
            assert len(errors) == 0, f"WS ASR returned errors: {errors}"
        finally:
            _api(f"/api/meetings/{mid}", "DELETE", token=shared_token)
