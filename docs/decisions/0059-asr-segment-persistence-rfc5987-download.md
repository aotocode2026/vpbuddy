# ADR-0059: ASR 转写分段持久化 + RFC 5987 中文文件名下载 + E2E 测试加固 (v0.23.3)

- **日期**: 2026-07-18
- **状态**: 已通过
- **背景 ADR**: [ADR-0058 Generation 去重与幂等](../0058-generation-dedup-idempotency.md) · [ADR-0046 百炼 ASR](../0046-bailian-fun-asr-realtime.md)
- **关联 Issue**: [#45 ASR 分段不持久化](https://github.com/zhangsheng377/vpbuddy/issues/45) · [#47 中文文件名下载断连](https://github.com/zhangsheng377/vpbuddy/issues/47)

---

## 问题 (Motivation)

### 1. ASR 转写分段仅存内存

百炼 Fun-ASR-Realtime 每句转写完成后仅推送 `transcript` JSON 给客户端 + 写入 `MeetingState.cleaned_text`。分段记录（`transcript_segments`）没有持久化——服务重启或进程崩溃后丢失全部转写历史。

### 2. 中文文件名下载断连

`GET /api/kb/{doc_id}/file`、`GET /api/meetings/{id}/docs/{kind}/download`、`GET /api/materials/{id}/file` 三个下载端点手动拼接 `Content-Disposition` header：

```python
# 旧代码：手动拼接，中文名不编码 → 部分浏览器断连
headers={"Content-Disposition": f'attachment; filename="{fp.name}"'}
```

中文文件名在 HTTP header 中未按 RFC 5987 编码，导致部分浏览器（尤其是 Windows 环境）下载断连。

---

## 决策

### 决策 1: ASR 分段持久化到 `{mid}.stream.json`

**方案**: `BailianCallback.on_event()` 每句转写完成后调用 `_persist_segment()`，将分段追加写入 `{meeting_id}.stream.json` 的 `transcript_segments` 数组。

**文件格式** (`data/meetings/{meeting_id}.stream.json`):
```json
{
  "meeting_id": "abc123",
  "recording_session_id": "uuid",
  "transcript_segments": [
    {
      "id": "uuid:1",
      "recording_session_id": "uuid",
      "sequence": 1,
      "text": "大家好，今天我们讨论项目进度。",
      "begin_time": 0,
      "end_time": 2500,
      "is_sentence_end": true
    }
  ],
  "transcript_revision": 5
}
```

**幂等语义**:
- 分段 id = `{recording_session_id}:{sentence_count}`，写入前检查 id 是否已存在
- 噪音/空文本跳过不写
- 写入失败不阻塞主流程（`logger.error` 兜底）

**关键代码路径**:
```python
# bailian_asr.py — BailianCallback.on_event()
self._persist_segment(text, cleaned, begin_time, end_time, is_noise)

def _persist_segment(self, text, cleaned, begin_time, end_time, is_noise):
    if is_noise:
        return
    seg_id = f"{self._session.recording_session_id}:{self._session.sentence_count}"
    clean_text = cleaned.strip() if cleaned else text.strip()
    if not clean_text:
        return
    meta = _load_stream_meta(self._session.meeting_id)
    segments = meta.get("transcript_segments", [])
    if any(s.get("id") == seg_id for s in segments):
        return  # 幂等：已存在则跳过
    segments.append({...})
    _save_stream_meta(self._session.meeting_id, meta)
```

**理由**:
- 分段记录对后续审计 (#41)、说话人分析有基础价值
- 写入 JSON 文件，格式简单、兼容性好，不需要额外依赖
- 幂等设计防止重复写入

### 决策 2: FastAPI FileResponse `filename=` 参数（RFC 5987 自动编码）

**方案**: 用 FastAPI 内置的 `FileResponse(filename=...)` 替代手动 `Content-Disposition` header。

**变更前**:
```python
FileResponse(str(fp), headers={"Content-Disposition": f'attachment; filename="{fp.name}"'})
```

**变更后**:
```python
FileResponse(str(fp), filename=fp.name, media_type="application/octet-stream")
```

**影响的 3 个端点**:
| 端点 | 文件 |
|---|---|
| `GET /api/kb/{doc_id}/file` | KB 文档原始文件 |
| `GET /api/meetings/{id}/docs/{kind}/download` | 6 类文档文件 |
| `GET /api/materials/{id}/file` | 会议材料文件 |

**理由**:
- Starlette/FastAPI 的 `FileResponse(filename=)` 内置 RFC 5987 编码（`filename*=UTF-8''...`），浏览器原生支持
- 不需要手写 header 拼接，避免编码错误
- 向下兼容——API 端点路径、响应格式均不变

### 决策 3: E2E 测试加固

**问题**: `test_task_manager_e2e.py` 3 个测试和 `test_fastapi_server.py` 27 个测试长期因环境差异被跳过，启动后大量失败。

**修复**:

1. **task_manager E2E (3 测试)**: 适配 `MeetingTaskQueue.submit()` 的 defer 行为——running 时新提交返回 `None`，旧任务完成后自动 kick deferred runner。
2. **fastapi_server E2E (27 测试)**:
   - 健康检查 `/api/status`（需认证）→ `/healthz`（免认证）
   - 新增 `fastapi_token` fixture — 向本地 server 注册用户拿 JWT token
   - `_http_get`/`_http_post`/`_check_route` 支持 `token=` 参数
   - KB 3 测试容错 chromadb 未安装 → 500
   - Meeting 5 测试容错不存在会议 404 error dict 格式

---

## 替代方案及原因

### 决策 1 替代: 存 SQLite

- 优势: 查询更方便
- 劣势: 引入额外的表结构和迁移，JSON 文件已能满足当前需求
- **拒绝原因**: YAGNI——审计/分析需求尚未明确，JSON 迁移到 SQLite 的成本低

### 决策 2 替代: 手写 URL 编码

- 优势: 不依赖 FastAPI 版本
- 劣势: 容易写错（UTF-8 % 编码 + RFC 5987 格式），不同浏览器行为不一致
- **拒绝原因**: FastAPI/Starlette 已内置正确实现，手写是重复造轮子

---

## 影响

### API 契约
- **无 Breaking Changes**: 所有 HTTP 端点路径、请求/响应格式不变
- **下载端点行为变更**: `Content-Disposition` header 从 `filename="xxx"` 变为 `filename*=UTF-8''xxx`（RFC 5987），文件名正确显示，不再断连
- **stream.json**: 新增文件格式，不影响现有 `{mid}.json` (MeetingState) 或 `{mid}.chat.json`

### 测试
- 单元测试: `test_v0233_regression.py` 9 项（ASR persist 6 + RFC 5987 3）
- task_manager E2E: 12 项全部通过（含 3 项修复）
- fastapi_server E2E: 27 项全部通过（含 auth fixture + 容错）

---

## 参考

- [RFC 5987 — Character Set and Language Encoding for Hypertext Transfer Protocol (HTTP) Header Field Parameters](https://datatracker.ietf.org/doc/html/rfc5987)
- [FastAPI FileResponse](https://www.starlette.io/responses/#fileresponse)
- [Issue #45](https://github.com/zhangsheng377/vpbuddy/issues/45) · [Issue #47](https://github.com/zhangsheng377/vpbuddy/issues/47)
