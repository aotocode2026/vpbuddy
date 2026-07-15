# ADR-0057: 事件驱动文档生成重构 (v0.23.1)

- **日期**: 2026-07-16
- **状态**: 已通过 (经 7 轮 DEBUG 验证)
- **背景 ADR**: [ADR-0042 task_manager](../0042-task-manager.md) · [ADR-0046 百炼 ASR](../0046-bailian-fun-asr-realtime.md)

---

## 问题 (Motivation)

v1.45-v1.50 的文档生成架构依赖两个轮询机制：

| 机制 | 行为 | 问题 |
|------|------|-----|
| **gkd daemon** (global_kick_docs) | 每 6s 扫描所有 298 个 meeting，hash 变化则 `task_manager.submit` | **300 会议积压**: 进程重启时 `_gkd_first=True` 全量提交 → 前 N 轮全是历史会议，当前活跃会议排队等几十秒 |
| **_kick_docs** (per-WS async task) | 每 6s 从 `MeetingStorage` 读 state，hash 变化则提交 | **空文本误触**: `len=0 hash=d41d8cd9` 触发空白文档；**延迟**: 用户说话 6s 后才触发 |

**用户症状**: 新会议录音后 docs/demo 迟迟不出现，日志显示 131 个历史会议排队触发 batch_docs。

---

## 决策 (Decision)

**将文档生成从"定时轮询"改为"事件驱动"**：

### 核心变更

| 模块 | 旧 | 新 |
|------|---|---|
| `bailian_asr.py` | `on_event()` 写完 state 就结束 | **新增 `on_state_changed` 回调**，每句完成后通知 |
| `fastapi_app.py` | 每 WS 连接创建 `_kick_docs()` async task 轮询 | **去掉轮询**，注入 `_on_state_changed` 闭包直接调 `task_manager.submit` |
| gkd daemon | `_gkd_first=True` 扫 298 个全表 | **只扫有 SSE 订阅者的会议** (`_subscribers`), 去掉 `_gkd_first` |
| `_write_state` | `st.exists(mid)=True` 才写 | **不存在则创建 `MeetingState(meeting_id=mid)`** |
| `task_manager` | `max_workers=4` | `max_workers=8` |

### 事件流

```
说话 → 百炼 WS callback → sentence_end → _write_state → on_state_changed → task_manager.submit
                                                              │
                                                     MeetingTaskQueue.submit
                                                     ├─ idle → 提交 executor
                                                     ├─ running → defer (_pending_runner)
                                                     └─ completed → kick deferred
```

**安全保证**: `task_manager.MeetingTaskQueue.submit` 已有 per-meeting 锁 + defer 机制，多句连续触发不会堆积——running 时只存一份 `_pending_runner`，完成后自动 kick。

### 为什么不用 debounce

用户明确要求 **"不要任何硬性时间约束"**。per-meeting `MeetingsTaskQueue` 的 running→defer→kick 已经是最小等待（只等当前 LLM 调用结束），平均 < 20s。

---

## 回顾 (Review)

### 7 轮 DEBUG 根因日志

| # | 时间 | 日志关键行 | 根因 |
|---|------|-----------|------|
| 1 | 先 | `KeyError: 'demo_v3.html'` → SSE 崩溃 | `demo_version.py` list 过滤 bug — WQ1 已修复 |
| 2 | 00:18 | WS `connection lost, kept open for reconnect` — 未触发 doc gen | client 未发 stop → `_stop_received=False` → 跳过 `_close_meeting` |
| 3 | 17:10 | `_kick_docs meaningful change len=0 hash=d41d8cd9` — 空文本触发空白文档 | `_kick_docs` 初始 6s 延迟时 state 为空 |
| 4 | 17:40 | `on_state_changed submit failed: cannot access free variable 'get_task_manager'` × 6 | Python 闭包自由变量在 dashscope 回调线程中未绑定 |
| 5 | 17:45 | `NO STATE` — `st.exists(mid)=False` 导致 35 chars 写丢 | `_write_state` 不处理第一次写入 |
| 6 | 18:00 | 131 个历史 meeting 排队 batch_docs + demo timeout 180s | gkd `_gkd_first=True` 进程重启全表扫描 |
| 7 | 18:10 | demo 重复 v1 推送 5 次，`batch_docs` 每次调 LLM | 每句都 submit 没 debounce（靠 task_manager 排队，但 docs 完了都调 demo） |

### 最终稳定状态

- **正确性**: on_state_changed + _write_state 自动创建 MeetingState → state 永远存在
- **延迟**: ~1s (句子转写完成 → 落盘 → submit)，LLM 生成 ~15-25s / batch
- **不浪费**: task_manager 内部排队 + 每句都 try submit，内容无变化由 LLM 自行判断
- **断连恢复**: WS 断连 finally 块也 submit，下次重连 SSE 回放已有文档

---

## 后果 (Consequences)

### 好
- 零轮询，输入到达即处理
- 不堆积历史会议
- 首句就有文档（~20s vs 老 ~45s）
- gkd 从全表扫描降为 SSE-active-only

### 不好
- 每句都调 LLM（约 15-25s/次, MiniMax-M3 免费额度充裕）
- 依赖 task_manager 内部锁+defer 不堆积，无硬性节流
- 句很短（1-2 字）可能触发无意义的 batch_docs
