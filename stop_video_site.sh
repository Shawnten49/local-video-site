#!/bin/sh
set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

PORT="${LOCAL_VIDEO_PORT:-8766}"
BASE_URL="${LOCAL_VIDEO_URL:-http://127.0.0.1:${PORT}}"
HEALTH_URL="${BASE_URL}/api/health"
LOG_DIR="${LOCAL_VIDEO_LOG_DIR:-${PROJECT_DIR}/local_video_site/data/logs}"
PID_FILE="${LOCAL_VIDEO_PID_FILE:-${PROJECT_DIR}/local_video_site/data/video-site.pid}"
KEEP_AWAKE_PID_FILE="${LOCAL_VIDEO_KEEP_AWAKE_PID_FILE:-${PROJECT_DIR}/local_video_site/data/keep-awake.pid}"

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

usage() {
  cat <<EOF
用法: ./stop_video_site.sh

关闭监听在端口 ${PORT} 的本项目视频站。
使用自定义端口时:
  LOCAL_VIDEO_PORT=8767 ./stop_video_site.sh
EOF
}

for arg in "$@"; do
  case "$arg" in
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

listening_pids() {
  lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
}

is_caffeinate_pid() {
  pid="${1:-}"
  [ -n "$pid" ] || return 1
  command_name="$(ps -p "$pid" -o comm= 2>/dev/null | awk '{$1=$1};1')"
  [ "$(basename "${command_name:-}")" = "caffeinate" ]
}

stop_caffeinate_from_pid_file() {
  pid_file="$1"
  label="$2"
  [ -e "$pid_file" ] || return 0
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && is_caffeinate_pid "$pid"; then
    kill "$pid" 2>/dev/null || true
    log_info "已停止${label}: PID $pid"
  fi
  rm -f "$pid_file"
}

stop_keep_awake_processes() {
  stop_caffeinate_from_pid_file "$KEEP_AWAKE_PID_FILE" "防休眠进程"
  stop_caffeinate_from_pid_file "$PID_FILE" "后台防休眠包装进程"
}

is_local_video_site() {
  response="$(
    curl --silent --show-error --fail --max-time 2 "$HEALTH_URL" 2>/dev/null \
      || true
  )"
  printf '%s' "$response" \
    | grep -Eq '"service"[[:space:]]*:[[:space:]]*"local-video-site"'
}

pids="$(listening_pids)"
if ! is_local_video_site; then
  if [ -z "$pids" ]; then
    stop_keep_awake_processes
    log_info "本地视频站未运行: $BASE_URL"
    exit 0
  fi

  log_warn "拒绝关闭: 端口 $PORT 被非本地视频站程序占用。"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 1
fi

if [ -z "$pids" ]; then
  log_warn "无法找到本地视频站监听进程: 端口 $PORT"
  exit 1
fi

log_info "正在关闭本地视频站: $BASE_URL"
for pid in $pids; do
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
  fi
done

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  [ -z "$(listening_pids)" ] && break
  sleep 0.1
done

if [ -n "$(listening_pids)" ]; then
  log_warn "关闭失败: 端口 $PORT 仍有进程监听。"
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
  exit 1
fi

stop_keep_awake_processes
log_info "本地视频站已关闭"
