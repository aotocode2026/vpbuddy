#!/usr/bin/env bash
# VPBuddy deployment launcher.
# The persistent configuration source is /data/vpbuddy/.env.  The checkout-local
# .env is an exact, verified copy so replacing /data/vpbuddy/server is safe.

set -euo pipefail

PORT="${VPBUDDY_PORT:-8765}"
VPBUDDY_DIR="${VPBUDDY_DIR:-/data/vpbuddy/server}"
VENV="${VPBUDDY_VENV:-/data/vpbuddy/venv}"
LOG_DIR="${VPBUDDY_LOG_DIR:-/data/vpbuddy/logs}"
MASTER_ENV="${VPBUDDY_MASTER_ENV:-/data/vpbuddy/.env}"
SERVER_ENV="$VPBUDDY_DIR/.env"
SYNC_ONLY=false

if [[ "${1:-}" == "--sync-env-only" ]]; then
    SYNC_ONLY=true
elif [[ $# -gt 0 ]]; then
    echo "[错误] 未知参数: $1" >&2
    echo "用法: bash start_vpbuddy.sh [--sync-env-only]" >&2
    exit 2
fi

sync_env() {
    local target_dir temp_env

    if [[ ! -f "$MASTER_ENV" || ! -r "$MASTER_ENV" ]]; then
        echo "[错误] 主配置文件不存在或不可读: $MASTER_ENV" >&2
        return 1
    fi
    if [[ ! -d "$VPBUDDY_DIR" ]]; then
        echo "[错误] Server 目录不存在: $VPBUDDY_DIR" >&2
        return 1
    fi

    target_dir=$(dirname "$SERVER_ENV")
    temp_env=$(mktemp "$target_dir/.env.tmp.XXXXXX") || {
        echo "[错误] 无法在 $target_dir 创建临时配置文件" >&2
        return 1
    }
    trap 'rm -f "${temp_env:-}"' RETURN

    if ! install -m 600 "$MASTER_ENV" "$temp_env"; then
        echo "[错误] 无法复制 $MASTER_ENV" >&2
        return 1
    fi
    if ! cmp -s "$MASTER_ENV" "$temp_env"; then
        echo "[错误] 临时配置与主配置不一致" >&2
        return 1
    fi
    if ! mv -f "$temp_env" "$SERVER_ENV"; then
        echo "[错误] 无法写入 $SERVER_ENV" >&2
        return 1
    fi
    trap - RETURN

    if [[ ! -f "$SERVER_ENV" ]] || ! cmp -s "$MASTER_ENV" "$SERVER_ENV"; then
        echo "[错误] 配置同步后的校验失败: $SERVER_ENV" >&2
        return 1
    fi
    chmod 600 "$SERVER_ENV"
    echo "  ✓ 已验证同步: $MASTER_ENV -> $SERVER_ENV"
}

load_master_env() {
    local line key value
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" == export[[:space:]]* ]] && line="${line#export }"
        if [[ "$line" != *=* ]]; then
            echo "[错误] $MASTER_ENV 中存在无效配置行" >&2
            return 1
        fi
        key="${line%%=*}"
        value="${line#*=}"
        key="${key//[[:space:]]/}"
        if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            echo "[错误] $MASTER_ENV 中存在无效变量名: $key" >&2
            return 1
        fi
        if [[ ${#value} -ge 2 && ( ( "$value" == \"*\" ) || ( "$value" == \'*\' ) ) ]]; then
            value="${value:1:${#value}-2}"
        fi
        export "$key=$value"
    done < "$MASTER_ENV"
}

echo ""
echo "  VPBuddy 一键启动"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

echo "[0/5] 同步并校验持久化配置..."
sync_env
load_master_env
export VPBUDDY_ENV_FILE="$MASTER_ENV"

if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
    echo "[错误] $MASTER_ENV 未配置 DASHSCOPE_API_KEY" >&2
    exit 1
fi
if [[ -z "${MINIMAX_API_KEY:-}" ]]; then
    echo "[错误] $MASTER_ENV 未配置 MINIMAX_API_KEY" >&2
    exit 1
fi
if [[ -z "${VPBUDDY_KB_DIR:-}" ]]; then
    echo "[错误] $MASTER_ENV 未配置 VPBUDDY_KB_DIR" >&2
    exit 1
fi
if [[ "$VPBUDDY_KB_DIR" != /* ]]; then
    echo "[错误] VPBUDDY_KB_DIR 必须是绝对路径: $VPBUDDY_KB_DIR" >&2
    exit 1
fi
export BAILIAN_API_KEY="${BAILIAN_API_KEY:-$DASHSCOPE_API_KEY}"
export MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-https://api.minimax.chat/v1}"
echo "  ✓ 百炼与 MiniMax 配置均已加载（密钥不输出）"

if $SYNC_ONLY; then
    echo "  ✓ 配置同步检查完成"
    exit 0
fi

echo "[1/5] 停止 admin-dashboard..."
ADMIN_PIDS=$(pgrep -f "admin.dashboard\|hermes.*dashboard\|admin-dashboard" 2>/dev/null || true)
if [[ -n "$ADMIN_PIDS" ]]; then
    echo "$ADMIN_PIDS" | xargs kill 2>/dev/null || true
    sleep 2
    ADMIN_PIDS=$(pgrep -f "admin.dashboard\|hermes.*dashboard\|admin-dashboard" 2>/dev/null || true)
    [[ -n "$ADMIN_PIDS" ]] && echo "$ADMIN_PIDS" | xargs kill -9 2>/dev/null || true
    echo "  ✓ admin-dashboard 已停止"
else
    echo "  - 未运行"
fi

echo "[2/5] 停止旧 VPBuddy 进程..."
VP_PIDS=$(pgrep -f "vpbuddy.*ui" 2>/dev/null || true)
if [[ -n "$VP_PIDS" ]]; then
    echo "$VP_PIDS" | xargs kill 2>/dev/null || true
    sleep 3
    VP_PIDS=$(pgrep -f "vpbuddy.*ui" 2>/dev/null || true)
    [[ -n "$VP_PIDS" ]] && echo "$VP_PIDS" | xargs kill -9 2>/dev/null || true
    echo "  ✓ 旧 VPBuddy 进程已停止"
else
    echo "  - 未运行"
fi

echo "[3/5] 检查端口 $PORT..."
PORT_PID=$(lsof -ti:"$PORT" 2>/dev/null || true)
if [[ -n "$PORT_PID" ]]; then
    echo "$PORT_PID" | xargs kill -9 2>/dev/null || true
    sleep 1
    if lsof -ti:"$PORT" >/dev/null 2>&1; then
        echo "[错误] 无法释放端口 $PORT" >&2
        exit 1
    fi
fi
echo "  ✓ 端口 $PORT 可用"

echo "[4/5] 检查运行目录..."
if [[ ! -x "$VENV/bin/vpbuddy" ]]; then
    echo "[错误] VPBuddy 可执行文件不存在: $VENV/bin/vpbuddy" >&2
    exit 1
fi
mkdir -p "$LOG_DIR"
echo "  ✓ 运行目录可用"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/startup_$TIMESTAMP.log"
echo "[5/5] 启动 VPBuddy..."
cd "$VPBUDDY_DIR"
nohup "$VENV/bin/vpbuddy" ui --port "$PORT" > "$LOG_FILE" 2>&1 &
PID=$!
echo "  → PID: $PID"
echo "  → 日志: $LOG_FILE"

echo -n "  → 等待服务就绪"
for _ in $(seq 1 30); do
    sleep 2
    CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)
    if [[ "$CODE" == "200" ]]; then
        echo ""
        echo "  ✓ VPBuddy 启动成功（PID $PID，端口 $PORT）"
        exit 0
    fi
    if ! kill -0 "$PID" 2>/dev/null; then
        echo ""
        echo "[错误] VPBuddy 进程提前退出，请查看: $LOG_FILE" >&2
        exit 1
    fi
    echo -n "."
done

echo ""
echo "[错误] 启动超时，请查看: $LOG_FILE" >&2
exit 1
