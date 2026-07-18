# ADR-0058: 输入哈希去重 + Finalize 幂等 + Generation 跟踪 (v0.23.2)

- **日期**: 2026-07-18
- **状态**: 已通过
- **背景 ADR**: [ADR-0057 事件驱动文档生成](../0057-event-driven-doc-gen.md) · [ADR-0024 Demo 版本化](./0024-demo-multiversion.md)
- **关联 Issue**: [#44 P0 Scheduler/Versioning](https://github.com/zhangsheng377/vpbuddy/issues/44)

---

## 问题 (Motivation)

v0.23.1 完成了事件驱动文档生成，但仍有三个问题：

### 1. Stop 后仍持续生成

会议 `3333` 日志：
```
15:56:27 stop_capture 完成
15:56:27 连续收到 3 次 meeting-complete
15:56:32 生成 Demo v31
此后继续生成到 v53
```

根因：`_finalized_meetings` 是内存字典，重启后丢失；多个 stop 调用路径（HTTP `/close` + WS stop 帧）各自触发。

### 2. 同一内容重复版本化

```
v33 18695 bytes
v34 18695 bytes
v35 18695 bytes
```

根因：多个触发点（ASR / GKD / Chat 上传）独立 `task_manager.submit`，没有输入级去重。

### 3. 输入无变化时仍跑 Agent

每句转写完成都触发 `task_manager.submit()`，即使 `cleaned_text` 只加了几个字也跑完整 batch_docs + demo（每次 1-2 min LLM 调用）。

---

## 决策 (Decision)

新增 `src/vpbuddy/generation.py` 模块，实现三层去重：

### 1. 输入哈希去重（`compute_input_hash`）

计算 sha256 规范化摘要，包含：
- `cleaned_text`（转写文本）
- 5 类累积项（text + priority + status，排除 ids/timestamps）
- 上传文件列表（名称 + 大小）
- 最近 20 条 chat 消息

排除：`last_updated`、`speaker_map`、IDs、版本号。

**`task_manager.submit()` 提交前调用 `should_skip_generation()`**：若 hash 与上次 completed generation 相同，跳过提交。

### 2. Finalize 持久化（`mark_finalized` / `is_finalized`）

将 finalize 状态从内存字典改为磁盘文件 `data/meetings/{id}.finalized`：
- 重启后仍有效
- `mark_finalized()` 内部检查文件是否存在 → `is_repeat: True` 时复用
- `_close_meeting()` 调用后，`should_skip_generation()` 永久返回 "finalized"

### 3. Generation 跟踪（`create_generation` / `complete_generation`）

每次文档生成创建记录到 `data/meetings/generations/{id}.json`：
- `idempotency_key`: `{mid}:docs:r{N}:{hash_prefix}`
- `input_hash` + `output_hash`
- `revision`: completed generations 计数 + 1
- `status`: queued → running → completed / unchanged / stale / failed

### 4. Demo 发布锁（`write_demo_version`）

为每个 meeting 加 `threading.Lock`，锁内：
- 二次 TOCTOU 去重检查
- 版本号推进 + 文件写入 + manifest 更新 + symlink（原子化）

### 数据流

```
触发点 (ASR / GKD / Chat / Close)
  → task_manager.submit()
    → should_skip_generation()
      ├─ finalized? → skip
      └─ input_unchanged? → skip
  → MeetingTaskQueue.submit() (debounce: running→defer)
  → run_docs(gen_id, mid)
    → create_generation() (input hash + idempotency key)
      ├─ unchanged? → skip agent, return "input_unchanged"
    → batch_docs agent
    → demo agent
    → write_demo_version() (under publish lock)
    → complete_generation() (output hash + status)
```

---

## 替代方案 (Alternatives Considered)

| 方案 | 否决原因 |
|------|---------|
| "等待 N 秒才允许生成" | 用户明确拒绝硬性时间约束 (#44 原则 1) |
| 在 ASR 回调层做 dedup | 只覆盖 ASR 触发点，Chat/Material 触发点仍可重复 |
| SQLite generation 表 | 过度设计 — JSON 文件足够（每会议 < 100 条记录） |
| 全量 ContextRevision 快照 | 过早抽象 — 当前 LLM agent 直接读 MeetingState + 文件 |

---

## 影响 (Consequences)

- **用户感知**: Stop 后不再有新版本生成；同一内容不产生重复版本号
- **LLM 成本**: 输入无变化时跳过 agent 调用，减少约 30-50% 无意义 LLM 调用
- **运维**: `.finalized` 文件永久存在，需手动删除才能重新对已结束会议生成文档
- **兼容性**: 无破坏性变更 — 老会议无 `.finalized` 文件，行为不变

---

## 验收标准

- [x] 同一输入多次 ASR 触发只产生一个 generation
- [x] Stop 后 `should_skip_generation()` 返回 "finalized"
- [x] 重复 Stop 不重复发送 `meeting-complete`
- [x] 输入无变化时 `run_docs` 返回 `input_unchanged`
- [x] `write_demo_version` 锁内 TOCTOU 去重生效
- [x] Generation 记录持久化，包含 idempotency_key
- [x] 重启后 `.finalized` 文件仍有效
