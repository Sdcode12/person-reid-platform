#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"
BACKEND_PID_FILE="$RUN_DIR/backend.pid"
BACKEND_LOG_FILE="$RUN_DIR/backend.log"
WEB_PID_FILE="$RUN_DIR/web.pid"
WEB_LOG_FILE="$RUN_DIR/web.log"
BACKEND_PORT=8002
WEB_PORT=5173
BACKEND_PATTERN="$ROOT_DIR/backend/.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port $BACKEND_PORT"
WEB_PATTERN="$ROOT_DIR/web/node_modules/.bin/vite --host 0.0.0.0 --port $WEB_PORT"
CAPTURE_STATE_FILE="$ROOT_DIR/backend/data/capture_runtime_state.json"
CONFIG_FILE="$ROOT_DIR/backend/config.yaml"
METADATA_FILE="$ROOT_DIR/hikvision_local_capture/photos/metadata.jsonl"
CAPTURE_AUDIT_FILE="$ROOT_DIR/backend/data/capture_config_audit.jsonl"
RUNTIME_CAPTURE_DIR="$ROOT_DIR/backend/data/capture_runtime_configs/photos"
LEGACY_CAPTURE_DIR="$ROOT_DIR/hikvision_local_capture/photos"
RUNTIME_CONFIG_DIR="$ROOT_DIR/backend/data/capture_runtime_configs"

mkdir -p "$RUN_DIR"
touch "$BACKEND_LOG_FILE" "$WEB_LOG_FILE"

enable_capture_autostart_state() {
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")"
  mkdir -p "$(dirname "$CAPTURE_STATE_FILE")"
  printf '{"desired_running": true, "desired_camera_ids": [], "updated_at": "%s"}\n' "$ts" >"$CAPTURE_STATE_FILE"
}

disable_capture_autostart_state() {
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")"
  mkdir -p "$(dirname "$CAPTURE_STATE_FILE")"
  printf '{"desired_running": false, "desired_camera_ids": [], "updated_at": "%s"}\n' "$ts" >"$CAPTURE_STATE_FILE"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

is_running() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  return 1
}

list_child_pids() {
  local parent_pid="$1"
  local child
  if ! command_exists pgrep; then
    return 0
  fi
  for child in ${(f)"$(pgrep -P "$parent_pid" 2>/dev/null || true)"}; do
    [[ -n "$child" ]] || continue
    echo "$child"
    list_child_pids "$child"
  done
}

list_matching_pids() {
  local pattern="$1"
  if ! command_exists pgrep; then
    return 0
  fi
  pgrep -f "$pattern" 2>/dev/null | awk 'NF && !seen[$0]++'
}

list_listening_pids() {
  local port="$1"
  if command_exists lsof; then
    lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NF && !seen[$0]++'
    return 0
  fi
  if command_exists netstat; then
    netstat -ltnp 2>/dev/null | awk -v port=":$port" '$4 ~ port && $6 == "LISTEN" { split($7, a, "/"); if (a[1] ~ /^[0-9]+$/) print a[1] }' | awk 'NF && !seen[$0]++'
  fi
}

terminate_pid_tree() {
  local pid="$1"
  local name="$2"
  local targets target
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  targets="$(
    {
      echo "$pid"
      list_child_pids "$pid"
    } | awk 'NF && !seen[$0]++'
  )"
  for target in ${(f)targets}; do
    kill "$target" 2>/dev/null || true
  done
  sleep 1
  for target in ${(f)targets}; do
    if kill -0 "$target" 2>/dev/null; then
      kill -9 "$target" 2>/dev/null || true
    fi
  done
  echo "$name stopped pid=$pid"
}

stop_orphaned() {
  local name="$1"
  local port="$2"
  local pattern="$3"
  local pids pid
  pids="$(list_matching_pids "$pattern")"
  if [[ -z "$pids" ]]; then
    pids="$(list_listening_pids "$port")"
  fi
  for pid in ${(f)pids}; do
    [[ -n "$pid" ]] || continue
    terminate_pid_tree "$pid" "$name (fallback)"
  done
}

start_backend() {
  local enable_capture="${1:-yes}"
  if [[ "$enable_capture" == "yes" ]]; then
    enable_capture_autostart_state
  fi
  if is_running "$BACKEND_PID_FILE"; then
    echo "backend already running pid=$(cat "$BACKEND_PID_FILE")"
    return
  fi
  (
    cd "$ROOT_DIR/backend"
    nohup env UV_CACHE_DIR=/tmp/uv-cache uv run --python .venv/bin/python uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" >>"$BACKEND_LOG_FILE" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )
  sleep 1
  echo "backend started pid=$(cat "$BACKEND_PID_FILE") log=$BACKEND_LOG_FILE"
}

mark_force_setup() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "backend config not found: $CONFIG_FILE"
    echo "reconfigure requires an existing config so the current database settings can be reused."
    exit 1
  fi
  (
    cd "$ROOT_DIR/backend"
    UV_CACHE_DIR=/tmp/uv-cache uv run --python .venv/bin/python python - <<'PY'
from app.core.settings import read_raw_config, write_raw_config

raw = read_raw_config()
app_cfg = dict(raw.get("app") or {})
app_cfg["setup_completed"] = False
app_cfg["force_setup"] = True
raw["app"] = app_cfg
path = write_raw_config(raw)
print(f"reconfigure armed: {path}")
PY
  )
}

start_web() {
  if is_running "$WEB_PID_FILE"; then
    echo "web already running pid=$(cat "$WEB_PID_FILE")"
    return
  fi
  (
    cd "$ROOT_DIR/web"
    nohup npm run dev -- --host 0.0.0.0 --port "$WEB_PORT" >>"$WEB_LOG_FILE" 2>&1 &
    echo $! >"$WEB_PID_FILE"
  )
  sleep 1
  echo "web started pid=$(cat "$WEB_PID_FILE") log=$WEB_LOG_FILE"
}

stop_one() {
  local pid_file="$1"
  local name="$2"
  if ! is_running "$pid_file"; then
    echo "$name not running"
    : >"$pid_file"
    return
  fi
  local pid
  pid="$(cat "$pid_file")"
  terminate_pid_tree "$pid" "$name"
  : >"$pid_file"
}

status_one() {
  local pid_file="$1"
  local name="$2"
  if is_running "$pid_file"; then
    echo "$name running pid=$(cat "$pid_file")"
  else
    echo "$name stopped"
  fi
}

show_logs() {
  echo "---- backend log (tail 40) ----"
  tail -n 40 "$BACKEND_LOG_FILE" || true
  echo "---- web log (tail 40) ----"
  tail -n 40 "$WEB_LOG_FILE" || true
}

file_size_bytes() {
  local target="$1"
  if [[ ! -f "$target" ]]; then
    echo "0"
    return
  fi
  wc -c <"$target" | tr -d ' '
}

print_release_file_state() {
  local label="$1"
  local target="$2"
  local size
  size="$(file_size_bytes "$target")"
  echo "$label: ${size}B ($target)"
}

print_release_dir_state() {
  local label="$1"
  local target="$2"
  local count="0"
  if [[ -d "$target" ]]; then
    count="$(find "$target" -type f | wc -l | tr -d ' ')"
  fi
  echo "$label: ${count} files ($target)"
}

runtime_configs_redacted() {
  local file
  for file in "$RUNTIME_CONFIG_DIR"/*.yaml; do
    [[ -e "$file" ]] || continue
    if ! awk '
      BEGIN {
        bad = 0
      }
      /^[[:space:]]*(host|username|password|stream_url_override|picture_url_override):/ {
        value = $0
        sub(/^[^:]+:[[:space:]]*/, "", value)
        gsub(/[[:space:]]/, "", value)
        if (value != "" && value != "\x27\x27" && value != "\"\"" && value != "null" && value != "~") {
          bad = 1
        }
      }
      END {
        exit bad
      }
    ' "$file"; then
      return 1
    fi
  done
  return 0
}

audit_release() {
  echo "---- release audit ----"
  print_release_file_state "backend log" "$BACKEND_LOG_FILE"
  print_release_file_state "web log" "$WEB_LOG_FILE"
  print_release_file_state "legacy metadata" "$METADATA_FILE"
  print_release_file_state "capture config audit" "$CAPTURE_AUDIT_FILE"
  print_release_dir_state "runtime capture photos" "$RUNTIME_CAPTURE_DIR"
  print_release_dir_state "legacy capture photos" "$LEGACY_CAPTURE_DIR"
  if runtime_configs_redacted; then
    echo "runtime capture yaml: redacted"
  else
    echo "BLOCKER runtime capture yaml still contains non-empty sensitive fields"
  fi
  if [[ -f "$CONFIG_FILE" && -s "$CONFIG_FILE" ]]; then
    echo "BLOCKER local backend config exists: $CONFIG_FILE"
    echo "  publish-safe release should recreate this through /setup or local env injection."
  else
    echo "backend config: not present"
  fi
  echo "sanitize-release only clears logs/metadata/audit. It does not touch backend/config.yaml or local image files."
}

sanitize_release() {
  if is_running "$BACKEND_PID_FILE" || is_running "$WEB_PID_FILE"; then
    echo "warning: backend/web still running; logs may be recreated immediately after sanitize-release"
  fi
  : >"$BACKEND_LOG_FILE"
  : >"$WEB_LOG_FILE"
  : >"$METADATA_FILE"
  : >"$CAPTURE_AUDIT_FILE"
  echo "cleared logs, metadata, and capture audit history"
  audit_release
}

cmd="${1:-status}"
case "$cmd" in
  start)
    start_backend
    start_web
    ;;
  stop)
    disable_capture_autostart_state
    stop_one "$WEB_PID_FILE" "web"
    stop_one "$BACKEND_PID_FILE" "backend"
    stop_orphaned "web" "$WEB_PORT" "$WEB_PATTERN"
    stop_orphaned "backend" "$BACKEND_PORT" "$BACKEND_PATTERN"
    ;;
  restart)
    disable_capture_autostart_state
    stop_one "$WEB_PID_FILE" "web"
    stop_one "$BACKEND_PID_FILE" "backend"
    stop_orphaned "web" "$WEB_PORT" "$WEB_PATTERN"
    stop_orphaned "backend" "$BACKEND_PORT" "$BACKEND_PATTERN"
    start_backend
    start_web
    ;;
  reconfigure)
    disable_capture_autostart_state
    stop_one "$WEB_PID_FILE" "web"
    stop_one "$BACKEND_PID_FILE" "backend"
    stop_orphaned "web" "$WEB_PORT" "$WEB_PATTERN"
    stop_orphaned "backend" "$BACKEND_PORT" "$BACKEND_PATTERN"
    mark_force_setup
    start_backend no
    start_web
    echo "reconfigure mode ready: open http://127.0.0.1:5173/setup to initialize the current config"
    ;;
  status)
    status_one "$BACKEND_PID_FILE" "backend"
    status_one "$WEB_PID_FILE" "web"
    ;;
  logs)
    show_logs
    ;;
  audit-release)
    audit_release
    ;;
  sanitize-release)
    sanitize_release
    ;;
  *)
    echo "usage: $0 {start|stop|restart|reconfigure|status|logs|audit-release|sanitize-release}"
    exit 2
    ;;
esac
