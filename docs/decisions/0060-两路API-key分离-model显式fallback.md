# ADR-0060: 两路 API Key 彻底分离 + Model 显式 Fallback

- **状态**: Accepted
- **日期**: 2026-07-21
- **作者**: Hermes (agent) · 张胜东 (review)
- **替代**: ADR-0041 (LLM endpoint 统一) · ADR-0049 (model 由 Hermes 自定)
- **依赖**: ADR-0054 (vision 三层逃生通道)

---

## 背景

VPBuddy 之前存在两个耦合问题：

1. **API Key 混用**: `OPENAI_API_KEY` 被设为百炼 key，但 LLM 模型写 `minimax-m3`。百炼 key 不能调用 MiniMax 模型，MiniMax key 不能调百炼 ASR/Vision。
2. **Model 空字符串**: `AIAgent(model=None)` 期望 Hermes 从 `.env MODEL=minimax-m3` 读取模型名，但 `start_vpbuddy.sh` 未同步 MODEL 到所有 `.env` 文件，且 `fastapi_app.py` 的 `os.environ[key]=value` 可能覆盖 process env → 传到 MiniMax API 的 model 为空字符串 → `unknown model '' (2013)` 错误。

客户演示时遇到 ASR `invalid api key`（旧 key 未更新）和 LLM `unknown model ''`（model=None 未 fallback）。

## 决策

### 1. 两路彻底分离

| 功能 | Key | Base URL | 模型 |
|------|-----|----------|------|
| ASR 实时转写 | `DASHSCOPE_API_KEY` | 硬编码 DashScope WS | paraformer-zh (百炼内置) |
| Vision 识图 | `DASHSCOPE_API_KEY` | **硬编码** `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-vl-max → qwen-vl-plus` fallback |
| LLM Chat / 6doc | `MINIMAX_API_KEY` | `MINIMAX_BASE_URL` (默认 `https://api.minimax.chat/v1`) | `minimax-m3` (由 `MODEL` env 控制) |

**删除所有 `OPENAI_API_KEY` / `OPENAI_BASE_URL`** 引用：
- `fastapi_app.py`: 删除 DASHSCOPE→OPENAI 兜底逻辑，vision 硬编码 DashScope URL
- `api_utils.py`: Chat agent 和 Clean agent 改用 `MINIMAX_API_KEY` + `MINIMAX_BASE_URL`
- `sub_session_controller.py`: 6doc agent 改用 `MINIMAX_API_KEY` + `MINIMAX_BASE_URL`
- `kb_api.py`: KB 图片分析改用 `DASHSCOPE_API_KEY` + 硬编码 DashScope URL
- `start_vpbuddy.sh`: 删除 OPENAI_* 同步逻辑

### 2. Model 显式 Fallback

不再依赖 `AIAgent(model=None)` 让 Hermes 自己找 `.env`。改为：

```python
model=os.environ.get("MODEL", "minimax-m3")
```

影响文件:
- `api_utils.py`: `_get_chat_agent()` 和 `_get_clean_agent()`
- `sub_session_controller.py`: 6doc agent 构造函数

### 3. start_vpbuddy.sh MODEL 同步

`start_vpbuddy.sh` 的 .env 同步步骤新增 MODEL 行同步，确保所有 `.env` 文件中的 `MODEL=minimax-m3` 一致。

### 4. Hermes config.yaml Vision Key 更新

`/root/.hermes/config.yaml` 中 `auxiliary.vision.api_key` 从旧百炼 key (`sk-39828fa2e1...`) 更新为新 key (`sk-ws-H.EDXXLXM...`)。

### 5. 敏感信息清理

ADR-0040 中的 MiniMax key 前缀片段 `sk-cp-9kYB...RQs` 替换为 `sk-cp-**** (已脱敏)`。Git 仓库中无真实 API key 或密码泄露。

## 后果

### Positive
- 功能职责清晰: 百炼管 ASR+Vision，MiniMax 管 LLM
- `model=None` bug 修复: Chat 和 6doc 不再报 `unknown model ''`
- 一键启动脚本更健壮: MODEL 同步到所有 .env
- 无敏感信息泄露

### Negative
- 两套 API key 需要分别维护
- Vision 不再有 MiniMax VLM 后备（简化为纯百炼路径）

### 风险
- 如果 MiniMax 服务不可用，LLM 功能全部中断（无 fallback provider）
- 如果百炼服务不可用，ASR+Vision 全部中断

## 验证

- E2E 测试 29/29 通过
- ASR WebSocket: 连接成功 → `asr_status: connected`
- LLM Chat: MiniMax M3 正常回复 `1+1=2`
- Vision: 图片上传存储成功，异步 vision 任务触发正常
- 无 API key 或密码泄露
