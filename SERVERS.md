# VPBuddy 项目环境与服务器信息

> 最后更新: 2026-07-20

---

## 1. 项目地址

- **GitHub**: https://github.com/zhangsheng377/vpbuddy/
- **最新版本**: v0.23.3
- **描述**: 本地优先的会议操作系统级 AI 助手

---

## 2. 本机 (Windows 开发机)

| 项目 | 信息 |
|------|------|
| 操作系统 | Windows |
| 角色 | 客户端端到端测试 + 代码编写 |
| 本地项目路径 | `C:\Users\43587\Desktop\codes\vpbuddy\` |
| 客户端技术 | Tauri 2.6+ (Rust) - 编译产物为 .exe |
| 服务端组件 | Python 后端，在本机运行 `vpbuddy ui` 启动 Web UI |

### 2.1 快速命令

```powershell
# 启动 (克隆后)
cd C:\Users\43587\Desktop\codes\vpbuddy
pip install -e .
vpbuddy ui                 # 启动 Web UI (port 8765)
vpbuddy controller --start # 启动后台 controller
```

### 2.2 客户端编译 (Rust/Tauri)

```powershell
cd vpbuddy-client
cargo build --release       # 编译客户端
cargo test --lib            # 运行 Rust 单元测试 (当前 17 个)
```

---

## 3. Linux 开发服务器

| 项目 | 信息 |
|------|------|
| IP 地址 | `192.168.10.5` |
| 用户 | `zsd` |
| 密码 | `<开发机密码>` |
| SSH 端口 | 22 (默认) |
| 角色 | Linux 端开发/编译/测试 |
| 项目路径 | 需寻找（之前应该已有 `vpbuddy` 目录） |

### 3.1 快速连接

```bash
ssh zsd@192.168.10.5
# 密码: <开发机密码>
```

### 3.2 注意事项

- 用于 cargo/Rust 编译（Linux 环境下 Tauri 编译、测试等）
- 之前的开发文件应该已经存在，需要查找确认

---

## 4. 公网 GPU 服务器

| 项目 | 信息 |
|------|------|
| IP 地址 | `47.100.182.3` |
| 用户 | `root` |
| 密码 | `<GPU服务器密码>` |
| SSH 端口 | `16159` |
| 角色 | VP Buddy 服务端部署 (GPU 加速) |
| 内部端口 | `8765` |
| 公网端口 | `28765` (映射到内网 8765) |

### 4.1 快速连接

```bash
ssh -p 16159 root@47.100.182.3
# 密码: <GPU服务器密码>
```

### 4.2 服务访问

服务端 Web UI 通过公网访问:
```
http://47.100.182.3:28765
```

### 4.3 注意事项

- GPU 加速运行 ASR (百炼 Fun-ASR-Realtime WS) 和 embedding
- 部署了 hermes-agent + vpbuddy
- 客户端默认连接此公网地址
- **一键启动**: `bash /data/vpbuddy/server/start_vpbuddy.sh`
- **API Key 配置**: `/data/vpbuddy/.env` 中的 `DASHSCOPE_API_KEY`

---

## 5. 架构概览

```
┌─────────────────────────────────────────────────────┐
│ VP 桌面客户端 (Ubuntu / macOS / Windows)              │
├─────────────────────────────────────────────────────┤
│ Audio loopback (PipeWire / WASAPI / BlackHole)      │
│ ↓                                                    │
│ ASR (百炼 Fun-ASR-Realtime WS)                       │
│ ↓                                                    │
│ MeetingState (5 类事实累积)                           │
│ ↓                                                    │
│ 2 × sub_session (in-process AIAgent)                │
│ ┌──────────┬──────┐                                  │
│ │batch_docs│ demo │                                  │
│ └──────────┴──────┘                                  │
│ ↓                                                    │
│ Knowledge Base (Chroma + sentence-transformers)      │
│ ↓                                                    │
│ Web UI (FastAPI + Vanilla JS, port 8765)            │
└─────────────────────────────────────────────────────┘
         │
         ▼ (可选)
┌────────────────────────┐
│ GPU 服务器 (CUDA)       │
│ ASR/Embedding 加速      │
└────────────────────────┘
```

---

## 6. 关键文档索引

| 文档 | 链接 |
|------|------|
| README | [README.md](./README.md) |
| 总体架构 | [docs/design/总体架构.md](./docs/design/总体架构.md) |
| 产品需求 | [docs/product-spec/](./docs/product-spec/) |
| 决策记录(ADR) | [docs/decisions/](./docs/decisions/) |
| 安装指南 | [docs/部署/INSTALL.md](./docs/部署/INSTALL.md) |
| 模型切换 | [docs/部署/MODEL_SWAP.md](./docs/部署/MODEL_SWAP.md) |
| 踩坑记录 | [docs/部署/踩坑记录.md](./docs/部署/踩坑记录.md) |
| 用户手册 | [docs/用户手册.md](./docs/用户手册.md) |
