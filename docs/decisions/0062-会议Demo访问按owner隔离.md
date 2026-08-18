# ADR-0062：会议 Demo 访问按 owner 隔离

- 状态：Accepted
- 日期：2026-08-18
- 作者：Codex
- 依赖：ADR-0024、ADR-0050、ADR-0061

## 背景

会议状态、交付物和下载接口已经通过 `_require_meeting_owner()` 校验当前用户，
但仍有三条旁路：Demo 版本列表只验证 Bearer token，没有验证会议 owner；整个
`VPBUDDY_DOCS_DIR` 又通过 `/docs/*` 作为公开静态目录挂载。知道 meeting_id 和
文件名的其他用户可以绕过会议级权限读取 Demo 或其他会议文档；实时 ASR
WebSocket 也只验证 JWT，没有验证连接者是否拥有该会议。

行业模板功能会复用现有 Demo 生命周期，因此必须先消除该旁路，保证模板生成的
会议副本与普通会议遵守同一隔离边界。

## 决策

1. `GET /api/meetings/{id}/demo/versions` 增加会议 owner 校验。
2. 新增 `GET /api/meetings/{id}/demo/versions/{version}/content`，在 owner 校验后
   从 manifest 中确认版本存在，再返回对应 HTML。
3. 版本路径由服务端根据整数版本号构造，不接受客户端传入文件名，也不信任
   manifest 中可被篡改的相对路径。
4. 删除 `/docs/*` 的公开 `StaticFiles` 挂载。运行时文档目录只作为内部存储。
5. HTML 内容响应使用 `Cache-Control: private, no-store`。前端通过 Bearer fetch
   获取内容后，用 Blob URL 或 iframe `srcdoc` 渲染。
6. `/api/meetings/{id}/realtime_asr` 在 WebSocket handshake 后、启动 ASR session
   前校验会议 owner；非 owner 发送 403 错误并以 4403 关闭连接。

## 被否决的方案

- 在 `/docs/*` 查询参数中携带长期 JWT：URL 会进入浏览器历史、代理和访问日志。
- 只给版本列表加 owner 校验：已知文件路径仍可通过静态入口直接读取。
- 依赖不可猜测的 meeting_id：标识符不是授权机制。

## 影响

- 后端安全边界完整，但旧前端直接引用 `/docs/{meeting_id}/demo_vN.html` 的预览方式
  不再可用，前端必须切换到鉴权 fetch。
- 文档下载接口保持不变，仍通过 Bearer token 和 owner 校验返回附件。
- 部署前后必须验证 owner 为 200、非 owner 为 403、未登录为 401、旧 `/docs/*`
  为 404、跨用户 WebSocket 被拒绝，并验证现有会议 Demo 内容未迁移或删除。

