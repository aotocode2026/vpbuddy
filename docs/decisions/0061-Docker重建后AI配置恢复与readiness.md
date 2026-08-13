# 0061. Docker 重建后 AI 配置恢复与 readiness

- **状态**: Accepted
- **日期**: 2026-08-13
- **作者**: Codex / VPBuddy 维护者
- **替代**: 修正 ADR-0060 中仅依赖 `start_vpbuddy.sh` 同步配置的部署假设
- **依赖**: ADR-0060（百炼与 MiniMax 两路分离）

## 背景与问题

容器重建后出现百炼 ASR/图片正常、MiniMax Chat 与六份交付物全部失败的组合故障。根因不是 Docker `restart` 会删除环境变量，而是重建容器时 Compose 未显式注入 MiniMax 变量；应用的 `.env` fallback 还把 `/var/lib/vpbuddy/meetings` 错算成 `/var/lib/.env`，并会覆盖正确的容器环境变量。

旧的 `start_vpbuddy.sh` 还用 `DASHSCOPE_API_KEY` 是否存在控制所有配置加载。当百炼 key 已由外部注入、MiniMax 只在持久化 `.env` 时，MiniMax 配置会被整体跳过。

## 决策

1. 新增统一 `runtime_config`：Docker/systemd 进程环境变量优先；`.env` 只补缺失值，支持明确的 `VPBUDDY_ENV_FILE`。
2. 持久化配置候选从 `VPBUDDY_DATA_DIR` 的直接父目录计算，即 `/var/lib/vpbuddy/.env`。
3. Compose 显式注入并要求 `DASHSCOPE_API_KEY`、`MINIMAX_API_KEY` 和 JWT secret；MiniMax URL、模型提供明确默认值。
4. `.dockerignore` 排除所有真实 `.env`，禁止 secret 进入 build context 或镜像层。
5. `/healthz` 仅表示进程存活；新增 `/readyz`，分别报告 DashScope、MiniMax 和交付物配置状态，缺失任一路返回 HTTP 503，且不返回 key。
6. `start_vpbuddy.sh` 对每个变量独立补缺失值，拒绝缺少 MiniMax key 的启动，并使用 `/readyz` 验收。
7. Hermes 返回的常见 HTTP 认证/模型错误转换为稳定的 `provider_error`，不再作为正常 AI 回答保存。

## 后果

- 容器重建与裸机脚本启动使用同一配置优先级语义。
- 生产部署缺少 MiniMax 配置时不会再显示假健康。
- 修改 `.env` 后必须重建容器或重启进程，使 agent 缓存使用新配置。
- readiness 验证“完整配置”，不在每次探测时发送付费模型请求；外部 API 可达性仍由真实业务调用及日志监控判断。

## 回滚

回滚本 ADR 对应提交并重新创建容器。不要删除 `vpbuddy_data` 卷，回滚不会删除账号、会议或交付物数据。
