#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

PORT="${LOCAL_VIDEO_PORT:-8766}"
LOG_DIR="${LOCAL_VIDEO_LOG_DIR:-${PROJECT_DIR}/local_video_site/data/logs}"
PID_FILE="${LOCAL_VIDEO_PID_FILE:-${PROJECT_DIR}/local_video_site/data/video-site.pid}"
KEEP_AWAKE_PID_FILE="${LOCAL_VIDEO_KEEP_AWAKE_PID_FILE:-${PROJECT_DIR}/local_video_site/data/keep-awake.pid}"
BACKGROUND_LOG="${LOCAL_VIDEO_BACKGROUND_LOG:-${LOG_DIR}/background-$(date '+%Y-%m-%d').log}"
KEEP_AWAKE_LOG="${LOCAL_VIDEO_KEEP_AWAKE_LOG:-${LOG_DIR}/keep-awake-$(date '+%Y-%m-%d').log}"
OPEN_BROWSER=0
RESTART=0
LAN_MODE=0
BACKGROUND=0
KEEP_AWAKE=0
CAFFEINATE_BIN=""

log_message() {
  level="$1"
  message="$2"
  stamp="$(date '+%Y-%m-%d %H:%M:%S')"
  line="${stamp} | ${level} | local_video_site.launcher | ${message}"
  printf '%s\n' "$line"
  mkdir -p "$LOG_DIR"
  printf '%s\n' "$line" >> "${LOG_DIR}/server-$(date '+%Y-%m-%d').log"
}

log_info() {
  log_message INFO "$1"
}

log_warn() {
  log_message WARN "$1" >&2
}

terminal_message() {
  printf '%s | INFO | local_video_site.launcher | %s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

usage() {
  cat <<EOF
用法: ./start_video_site.sh [--lan] [--restart] [--background] [--keep-awake] [--open]

  --lan         仅允许同一局域网设备访问，并启用账号密码认证
  --restart     重启本项目的现有视频站（不会终止其他程序）
  --background  在后台启动并立即返回
  -b            同 --background
  --keep-awake  网站运行期间阻止 macOS 因空闲进入睡眠
  -k            同 --keep-awake
  --open        启动后显式打开浏览器；默认不打开
  --no-open     兼容旧参数；默认已经不会自动打开浏览器
EOF
}

for arg in "$@"; do
  case "$arg" in
    --restart)
      RESTART=1
      ;;
    --lan)
      LAN_MODE=1
      ;;
    --background|-b)
      BACKGROUND=1
      ;;
    --keep-awake|-k)
      KEEP_AWAKE=1
      ;;
    --open)
      OPEN_BROWSER=1
      ;;
    --no-open)
      OPEN_BROWSER=0
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      log_warn "未知参数: $arg"
      usage >&2
      exit 2
      ;;
  esac
done

resolve_caffeinate() {
  if [ -n "${LOCAL_VIDEO_CAFFEINATE:-}" ]; then
    CAFFEINATE_BIN="$LOCAL_VIDEO_CAFFEINATE"
  else
    CAFFEINATE_BIN="$(command -v caffeinate 2>/dev/null || true)"
  fi
  if [ -z "$CAFFEINATE_BIN" ] || [ ! -x "$CAFFEINATE_BIN" ]; then
    log_warn "当前系统不支持 caffeinate，无法启用防休眠。"
    exit 1
  fi
}

if [ "$KEEP_AWAKE" -eq 1 ]; then
  resolve_caffeinate
fi

HOST="127.0.0.1"
REQUESTED_MODE="local"
LAN_IP=""
if [ "$LAN_MODE" -eq 1 ]; then
  HOST="0.0.0.0"
  REQUESTED_MODE="lan"
  if [ -n "${LOCAL_VIDEO_LAN_IP:-}" ]; then
    LAN_IP="$LOCAL_VIDEO_LAN_IP"
  else
    LAN_IP="$(.venv/bin/python -m local_video_site lan-ip 2>/dev/null || true)"
  fi
  if [ -n "$LAN_IP" ]; then
    BASE_URL="${LOCAL_VIDEO_URL:-http://${LAN_IP}:${PORT}}"
  else
    BASE_URL="${LOCAL_VIDEO_URL:-http://127.0.0.1:${PORT}}"
    log_warn "未能自动检测局域网 IPv4 地址；服务仍会监听 0.0.0.0。"
    log_warn "可通过 LOCAL_VIDEO_LAN_IP=192.168.x.x 指定局域网地址。"
  fi
  export LOCAL_VIDEO_LAN_MODE=1
else
  BASE_URL="${LOCAL_VIDEO_URL:-http://127.0.0.1:${PORT}}"
  export LOCAL_VIDEO_LAN_MODE=0
fi
HEALTH_URL="http://127.0.0.1:${PORT}/api/health"
READY_URL="http://127.0.0.1:${PORT}/api/ready"

active_admin_count() {
  .venv/bin/python -c '
from local_video_site import database
from local_video_site.identity.bootstrap import ensure_bootstrap_admin

database.init_db()
ensure_bootstrap_admin()
print(database.active_admin_count())
'
}

default_admin_pending() {
  .venv/bin/python -c '
from local_video_site import database
from local_video_site.identity.bootstrap import default_admin_pending_change

database.init_db()
print(1 if default_admin_pending_change() else 0)
'
}

listening_pids() {
  lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
}

project_server_pids() {
  ps -axo pid=,command= 2>/dev/null \
    | while IFS= read -r process_line; do
        set -- $process_line
        pid="${1:-}"
        shift
        command="$*"
        [ -n "$pid" ] || continue
        case "$command" in
          *"-m local_video_site serve"*) ;;
          *) continue ;;
        esac
        case "$command" in
          *"--port ${PORT}"*) ;;
          *) continue ;;
        esac
        cwd="$(
          lsof -a -p "$pid" -d cwd -Fn 2>/dev/null \
            | sed -n 's/^n//p' \
            | head -n 1
        )"
        if [ "$cwd" = "$PROJECT_DIR" ]; then
          printf '%s\n' "$pid"
        fi
      done
}

service_pids() {
  {
    listening_pids
    project_server_pids
  } | awk 'NF && !seen[$1]++'
}

first_listening_pid() {
  for pid in $(listening_pids); do
    printf '%s\n' "$pid"
    return 0
  done
  return 1
}

is_caffeinate_pid() {
  pid="${1:-}"
  [ -n "$pid" ] || return 1
  command_name="$(ps -p "$pid" -o comm= 2>/dev/null | awk '{$1=$1};1')"
  [ "$(basename "${command_name:-}")" = "caffeinate" ]
}

is_keep_awake_pid() {
  pid="${1:-}"
  [ -n "$pid" ] || return 1
  is_caffeinate_pid "$pid" && return 0
  process_args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  case "$process_args" in
    *"local_video_site serve"*) return 0 ;;
    *) return 1 ;;
  esac
}

clear_stale_keep_awake_pid() {
  [ -e "$KEEP_AWAKE_PID_FILE" ] || return 0
  pid="$(cat "$KEEP_AWAKE_PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && is_keep_awake_pid "$pid"; then
    return 0
  fi
  rm -f "$KEEP_AWAKE_PID_FILE"
}

stop_keep_awake() {
  [ -e "$KEEP_AWAKE_PID_FILE" ] || return 0
  pid="$(cat "$KEEP_AWAKE_PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && is_keep_awake_pid "$pid"; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$KEEP_AWAKE_PID_FILE"
}

attach_keep_awake() {
  server_pid="${1:-}"
  if [ -z "$server_pid" ]; then
    log_warn "无法获取网站进程 PID，防休眠未启用。"
    exit 1
  fi
  mkdir -p "$LOG_DIR" "$(dirname "$KEEP_AWAKE_PID_FILE")"
  clear_stale_keep_awake_pid
  if [ -e "$KEEP_AWAKE_PID_FILE" ]; then
    keep_pid="$(cat "$KEEP_AWAKE_PID_FILE" 2>/dev/null || true)"
    log_info "防休眠已在运行: PID ${keep_pid}"
    return 0
  fi

  log_info "正在为网站附加防休眠: caffeinate -is -w $server_pid"
  nohup "$CAFFEINATE_BIN" -is -w "$server_pid" \
    >>"$KEEP_AWAKE_LOG" 2>&1 </dev/null &
  keep_pid=$!
  printf '%s\n' "$keep_pid" >"$KEEP_AWAKE_PID_FILE"
  sleep 0.2
  if ! kill -0 "$keep_pid" 2>/dev/null; then
    rm -f "$KEEP_AWAKE_PID_FILE"
    log_warn "防休眠进程启动失败。"
    tail -n 20 "$KEEP_AWAKE_LOG" >&2 2>/dev/null || true
    exit 1
  fi
  log_info "防休眠已启用: caffeinate -is"
  log_info "防休眠进程 PID: $keep_pid"
}

is_local_video_site() {
  response="$(
    curl --silent --show-error --fail --max-time 2 "$HEALTH_URL" 2>/dev/null \
      || true
  )"
  printf '%s' "$response" \
    | grep -Eq '"service"[[:space:]]*:[[:space:]]*"local-video-site"'
}

is_service_ready() {
  response="$(
    curl --silent --show-error --fail --max-time 2 "$READY_URL" 2>/dev/null \
      || true
  )"
  printf '%s' "$response" \
    | grep -Eq '"ready"[[:space:]]*:[[:space:]]*true'
}

current_service_mode() {
  response="$(
    curl --silent --show-error --fail --max-time 2 "$HEALTH_URL" 2>/dev/null \
      || true
  )"
  printf '%s' "$response" \
    | .venv/bin/python -c '
import json
import sys

try:
    print(json.load(sys.stdin).get("mode", "unknown"))
except Exception:
    print("unknown")
'
}

scan_existing_service() {
  .venv/bin/python -m local_video_site scan 2>&1 \
    | awk '
        /^discovered=/ { result = $0 }
        /^scan already running$/ { result = $0 }
        END { if (result != "") print result }
      ' \
    || true
}

print_lan_access() {
  if [ "$LAN_MODE" -eq 1 ]; then
    if [ -n "$LAN_IP" ]; then
      log_info "手机访问地址: $BASE_URL"
    else
      log_warn "请根据电脑当前 Wi-Fi IP，使用 http://电脑IP:${PORT} 访问。"
    fi
    terminal_message "权限: 游客仅浏览级别 0；普通用户可收藏并管理自己的下载任务；管理员拥有全部权限。"
    log_info "安全限制: 仅允许私有局域网和本机来源，公网来源将被拒绝。"
  fi
}

open_site() {
  if [ "$OPEN_BROWSER" -eq 1 ]; then
    if command -v open >/dev/null 2>&1; then
      open "$BASE_URL"
    else
      log_info "请手动打开: $BASE_URL"
    fi
  fi
}

launch_background() {
  mkdir -p "$LOG_DIR" "$(dirname "$PID_FILE")"
  if [ -e "$PID_FILE" ]; then
    stale_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$stale_pid" ]; then
      case " $(project_server_pids | tr '\n' ' ') " in
        *" $stale_pid "*)
          log_warn "后台 PID 文件对应的进程仍在运行，拒绝重复启动: $stale_pid"
          exit 1
          ;;
        *)
          rm -f "$PID_FILE"
          ;;
      esac
    else
      rm -f "$PID_FILE"
    fi
  fi

  log_info "正在后台启动本地视频站: $BASE_URL"
  log_info "后台输出日志: $BACKGROUND_LOG"
  set -- .venv/bin/python -m local_video_site serve \
    --host "$HOST" --port "$PORT"
  if [ "$OPEN_BROWSER" -eq 1 ]; then
    set -- "$@" --open
  fi
  background_pid="$(
    .venv/bin/python - "$BACKGROUND_LOG" "$@" <<'PY'
import subprocess
import sys

log_path = sys.argv[1]
command = sys.argv[2:]
with open(log_path, "ab", buffering=0) as log:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        close_fds=True,
        start_new_session=True,
    )
print(process.pid)
PY
  )"
  case "$background_pid" in
    ''|*[!0-9]*)
      rm -f "$PID_FILE"
      log_warn "后台启动失败：无法创建独立服务进程。"
      tail -n 30 "$BACKGROUND_LOG" >&2 2>/dev/null || true
      exit 1
      ;;
  esac
  printf '%s\n' "$background_pid" >"$PID_FILE"
  if [ "$KEEP_AWAKE" -eq 1 ]; then
    attach_keep_awake "$background_pid"
  fi
  log_info "后台进程 PID: $background_pid"

  attempts=0
  while [ "$attempts" -lt 240 ]; do
    if is_service_ready; then
      log_info "本地视频站已在后台运行: $BASE_URL"
      log_info "PID 文件: $PID_FILE"
      log_info "日志目录: $LOG_DIR"
      exit 0
    fi
    if ! kill -0 "$background_pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      if [ "$KEEP_AWAKE" -eq 1 ]; then
        rm -f "$KEEP_AWAKE_PID_FILE"
      fi
      log_warn "后台启动失败，进程已退出。最近日志:"
      tail -n 30 "$BACKGROUND_LOG" >&2 2>/dev/null || true
      exit 1
    fi
    attempts=$((attempts + 1))
    sleep 0.25
  done

  log_warn "后台进程健康检查未通过，正在停止未就绪的实例。"
  kill "$background_pid" 2>/dev/null || true
  stop_keep_awake
  rm -f "$PID_FILE"
  log_warn "最近日志:"
  tail -n 40 "$BACKGROUND_LOG" >&2 2>/dev/null || true
  exit 1
}

if [ "$LAN_MODE" -eq 1 ]; then
  if ! admin_count="$(active_admin_count 2>&1)"; then
    log_warn "局域网模式启动失败：无法检查管理员账号状态。"
    printf '%s\n' "$admin_count" >&2
    exit 1
  fi
  case "$admin_count" in
    ''|*[!0-9]*)
      log_warn "局域网模式启动失败：管理员账号检查返回了无效结果。"
      printf '%s\n' "$admin_count" >&2
      exit 1
      ;;
  esac
  if [ "$admin_count" -eq 0 ]; then
    log_warn "局域网模式启动失败：当前没有有效管理员账号。"
    log_warn "请先在本机启动服务，完成默认管理员初始化后再使用 --lan。"
    exit 1
  fi
  if [ "${LOCAL_VIDEO_ALLOW_DEFAULT_ADMIN_LAN:-0}" != "1" ]; then
    if [ "$(default_admin_pending 2>/dev/null || printf '1')" = "1" ]; then
      log_warn "局域网模式启动失败：默认管理员 admin 尚未修改初始密码。"
      log_warn "请先在本机启动并登录 admin，修改密码后再使用 --lan。"
      exit 1
    fi
  fi
fi

if is_local_video_site; then
  existing_mode="$(current_service_mode)"
  if [ "$existing_mode" != "$REQUESTED_MODE" ]; then
    log_info "检测到当前运行模式为 ${existing_mode}，正在切换为 ${REQUESTED_MODE}..."
    RESTART=1
  fi
  if [ "$RESTART" -eq 0 ] && ! is_service_ready; then
    log_warn "现有视频站健康检查未通过，正在自动重启..."
    RESTART=1
  fi
  if [ "$RESTART" -eq 0 ]; then
    log_info "本地视频站已运行: $BASE_URL"
    print_lan_access
    if [ "$KEEP_AWAKE" -eq 1 ]; then
      attach_keep_awake "$(first_listening_pid)"
    fi
    log_info "正在执行增量扫描..."
    scan_response="$(scan_existing_service)"
    case "$scan_response" in
      discovered=*)
        log_info "增量扫描完成: $scan_response"
        ;;
      *"scan already running"*)
        log_info "增量扫描已在运行，无需重复启动。"
        ;;
      "")
        log_warn "增量扫描没有返回结果，请查看日志。"
        ;;
      *)
        log_info "增量扫描结果: $scan_response"
        ;;
    esac
    log_info "日志目录: $LOG_DIR"
    open_site
    exit 0
  fi

  pids="$(service_pids)"
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  attempts=0
  while [ "$attempts" -lt 50 ]; do
    remaining=""
    for pid in $pids; do
      if kill -0 "$pid" 2>/dev/null; then
        remaining="$pid"
        break
      fi
    done
    [ -z "$remaining" ] && break
    sleep 0.2
    attempts=$((attempts + 1))
  done
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then
      log_warn "旧实例未在超时内退出，强制结束项目进程: $pid"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  attempts=0
  while [ "$attempts" -lt 25 ]; do
    remaining="$(service_pids)"
    [ -z "$remaining" ] && break
    sleep 0.2
    attempts=$((attempts + 1))
  done
  if [ -n "$(service_pids)" ]; then
    log_warn "重启失败: 本项目的旧服务进程仍未退出"
    exit 1
  fi
  stop_keep_awake
  rm -f "$PID_FILE"
  log_info "已停止旧视频站，正在启动最新版本..."
elif [ -n "$(listening_pids)" ]; then
  log_warn "启动失败: 端口 $PORT 已被其他程序占用。"
  log_warn "占用进程:"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  log_warn "请关闭该程序，或使用 LOCAL_VIDEO_PORT=其他端口 启动。"
  exit 1
else
  orphan_pids="$(project_server_pids)"
  if [ -n "$orphan_pids" ]; then
    log_warn "发现无监听但仍在运行的本项目服务进程，正在清理。"
    for pid in $orphan_pids; do
      kill "$pid" 2>/dev/null || true
    done
    attempts=0
    while [ "$attempts" -lt 50 ]; do
      remaining=""
      for pid in $orphan_pids; do
        if kill -0 "$pid" 2>/dev/null; then
          remaining="$pid"
          break
        fi
      done
      [ -z "$remaining" ] && break
      sleep 0.2
      attempts=$((attempts + 1))
    done
    for pid in $orphan_pids; do
      if kill -0 "$pid" 2>/dev/null; then
        log_warn "残留进程未在超时内退出，强制结束: $pid"
        kill -KILL "$pid" 2>/dev/null || true
      fi
    done
  fi
  log_info "正在启动本地视频站: $BASE_URL"
fi

print_lan_access
log_info "日志目录: $LOG_DIR"

if [ "$BACKGROUND" -eq 1 ]; then
  launch_background
fi

if [ "$KEEP_AWAKE" -eq 1 ]; then
  mkdir -p "$(dirname "$KEEP_AWAKE_PID_FILE")"
  printf '%s\n' "$$" >"$KEEP_AWAKE_PID_FILE"
  log_info "防休眠已启用: caffeinate -is"
  if [ "$OPEN_BROWSER" -eq 1 ]; then
    exec "$CAFFEINATE_BIN" -is .venv/bin/python -m local_video_site serve \
      --host "$HOST" --port "$PORT" --open
  fi
  exec "$CAFFEINATE_BIN" -is .venv/bin/python -m local_video_site serve \
    --host "$HOST" --port "$PORT"
fi

if [ "$OPEN_BROWSER" -eq 1 ]; then
  exec .venv/bin/python -m local_video_site serve \
    --host "$HOST" --port "$PORT" --open
fi
exec .venv/bin/python -m local_video_site serve \
  --host "$HOST" --port "$PORT"
