#!/bin/bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  VPBuddy 一键启动脚本                                         ║
# ║  用法: bash start_vpbuddy.sh                                 ║
# ║  功能: 杀旧进程 → 释放端口 → 设环境变量 → 启动 → 健康检查      ║
# ║  最后更新: 2026-07-20                                        ║
# ╚══════════════════════════════════════════════════════════════╝

set -e

PORT=8765
VPBUDDY_DIR="/data/vpbuddy/server"
VENV="/data/vpbuddy/venv"
LOG_DIR="/data/vpbuddy/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/startup_$TIMESTAMP.log"

echo ""
echo "  VPBuddy 一键启动"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# ── 0. 加载 API Key ──
# 两路分离 (v0.23.4):
#   百炼路: DASHSCOPE_API_KEY + BAILIAN_API_KEY → ASR + vision
#   MiniMax路: MINIMAX_API_KEY + MINIMAX_BASE_URL → LLM chat + 6doc
# 优先级: 环境变量 > /data/vpbuddy/.env > /data/vpbuddy/server/.env
for key in DASHSCOPE_API_KEY BAILIAN_API_KEY MINIMAX_API_KEY MINIMAX_BASE_URL MODEL; do
    [ -n "${!key:-}" ] && continue
    for envfile in /data/vpbuddy/.env /data/vpbuddy/server/.env; do
        [ -f "$envfile" ] || continue
        value=$("$VENV/bin/python" -c 'from dotenv import dotenv_values; import sys; print(dotenv_values(sys.argv[1]).get(sys.argv[2], "") or "")' "$envfile" "$key")
        if [ -n "$value" ]; then
            printf -v "$key" '%s' "$value"
            export "$key"
            break
        fi
    done
done

if [ -z "$DASHSCOPE_API_KEY" ]; then
    echo "[错误] DASHSCOPE_API_KEY 未设置！"
    echo "  请在 /data/vpbuddy/.env 中配置 DASHSCOPE_API_KEY=sk-ws-H.你的百炼key"
    exit 1
fi
echo "[0/5] 百炼 Key: ${DASHSCOPE_API_KEY:0:15}..."

# 衍生变量
export BAILIAN_API_KEY="${BAILIAN_API_KEY:-$DASHSCOPE_API_KEY}"
# MiniMax 默认值
export MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-https://api.minimax.chat/v1}"
export MODEL="${MODEL:-minimax-m3}"
if [ -z "$MINIMAX_API_KEY" ]; then
    echo "[ERROR] MINIMAX_API_KEY is required for Chat and deliverables."
    exit 1
fi

# ── 1. 杀 admin-dashboard（它会自动管理 vpbuddy，必须先杀）──
echo "[1/5] 停止 admin-dashboard..."
ADMIN_PIDS=$(pgrep -f "admin.dashboard\|hermes.*dashboard\|admin-dashboard" 2>/dev/null || true)
if [ -n "$ADMIN_PIDS" ]; then
    echo "  → 发现 admin-dashboard 进程: $ADMIN_PIDS"
    echo "$ADMIN_PIDS" | xargs kill 2>/dev/null || true
    sleep 2
    ADMIN_PIDS=$(pgrep -f "admin.dashboard\|hermes.*dashboard\|admin-dashboard" 2>/dev/null || true)
    if [ -n "$ADMIN_PIDS" ]; then
        echo "  → 强制终止: $ADMIN_PIDS"
        echo "$ADMIN_PIDS" | xargs kill -9 2>/dev/null || true
    fi
    echo "  ✓ admin-dashboard 已停止"
else
    echo "  - 未运行"
fi

# ── 2. 杀旧 vpbuddy ──
echo "[2/5] 停止旧 vpbuddy 进程..."
VP_PIDS=$(pgrep -f "vpbuddy.*ui" 2>/dev/null || true)
if [ -n "$VP_PIDS" ]; then
    echo "  → 发现 vpbuddy 进程: $VP_PIDS"
    echo "$VP_PIDS" | xargs kill 2>/dev/null || true
    sleep 3
    VP_PIDS=$(pgrep -f "vpbuddy.*ui" 2>/dev/null || true)
    if [ -n "$VP_PIDS" ]; then
        echo "  → 强制终止: $VP_PIDS"
        echo "$VP_PIDS" | xargs kill -9 2>/dev/null || true
    fi
    echo "  ✓ 旧 vpbuddy 进程已停止"
else
    echo "  - 未运行"
fi

# ── 3. 释放端口 ──
echo "[3/5] 检查端口 $PORT..."
PORT_PID=$(lsof -ti:$PORT 2>/dev/null || true)
if [ -n "$PORT_PID" ]; then
    echo "  → 端口被 PID $PORT_PID 占用，释放中..."
    kill -9 $PORT_PID 2>/dev/null || true
    sleep 1
    if lsof -ti:$PORT >/dev/null 2>&1; then
        echo "  ✗ 无法释放端口 $PORT！"
        exit 1
    fi
fi
echo "  ✓ 端口 $PORT 可用"

# ── 4. 更新 .env 文件（防止 fastapi_app.py 的 os.environ force-overwrite 覆盖 export）──
# v0.23.4: 两路分离 — 只同步百炼 + MiniMax 相关变量，不再有 OPENAI_*
echo "[4/5] 同步 .env 文件..."
for envfile in /data/vpbuddy/.env /data/vpbuddy/server/.env /data/vpbuddy/server/src/vpbuddy/server/.env; do
    if [ -f "$envfile" ]; then
        sed -i "s/^DASHSCOPE_API_KEY=.*/DASHSCOPE_API_KEY=$DASHSCOPE_API_KEY/" "$envfile" 2>/dev/null || true
        sed -i "s/^BAILIAN_API_KEY=.*/BAILIAN_API_KEY=$BAILIAN_API_KEY/" "$envfile" 2>/dev/null || true
        [ -n "$MINIMAX_API_KEY" ] && sed -i "s/^MINIMAX_API_KEY=.*/MINIMAX_API_KEY=$MINIMAX_API_KEY/" "$envfile" 2>/dev/null || true
        [ -n "$MINIMAX_BASE_URL" ] && sed -i "s/^MINIMAX_BASE_URL=.*/MINIMAX_BASE_URL=$MINIMAX_BASE_URL/" "$envfile" 2>/dev/null || true
        [ -n "$MODEL" ] && sed -i "s/^MODEL=.*/MODEL=$MODEL/" "$envfile" 2>/dev/null || true
        # 删除不再使用的 OPENAI_* 行
        sed -i "/^OPENAI_API_KEY=/d; /^OPENAI_BASE_URL=/d" "$envfile" 2>/dev/null || true
    fi
done
echo "  ✓ .env 文件已同步"

# ── 5. 启动 + 健康检查 ──
echo "[5/5] 启动 VPBuddy..."
cd "$VPBUDDY_DIR"
mkdir -p "$LOG_DIR"

nohup "$VENV/bin/vpbuddy" ui --port "$PORT" > "$LOG_FILE" 2>&1 &
PID=$!
echo "  → PID: $PID"
echo "  → 日志: $LOG_FILE"

# 等待启动
echo -n "  → 等待服务就绪"
for i in $(seq 1 30); do
    sleep 2
    CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/readyz" 2>/dev/null || echo "000")
    if [ "$CODE" = "200" ]; then
        echo ""
        echo ""
        echo "  ✓ VPBuddy 启动成功！"
        echo "    PID:    $PID"
        echo "    端口:   $PORT"
        echo "    内网:   http://127.0.0.1:$PORT"
        echo "    公网:   http://47.100.182.3:28765"
        echo "    日志:   $LOG_FILE"
        echo ""
        exit 0
    fi
    echo -n "."
done

echo ""
echo ""
echo "  ✗ 启动超时 (60s)"
echo "    请查看日志: tail -50 $LOG_FILE"
exit 1
