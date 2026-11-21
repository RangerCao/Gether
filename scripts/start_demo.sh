#!/usr/bin/env bash
# Gether 演示一键启动脚本（Windows Git Bash / macOS / Linux）
# 用法：
#   bash scripts/start_demo.sh start
#   bash scripts/start_demo.sh status
#   bash scripts/start_demo.sh stop
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$RUN_DIR/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

POSTGRES_PORT="${POSTGRES_PORT:-55432}"
GOVERNOR_PORT="${GOVERNOR_PORT:-8081}"
EXECUTOR_PORT="${EXECUTOR_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
POSTGRES_USER="${POSTGRES_USER:-gether}"
POSTGRES_DB="${POSTGRES_DB:-gether}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-gether_local_pwd}"
HMAC_SECRET="${CALLBACK_HMAC_SECRET:-change_me}"
JWT_SECRET="${JWT_SECRET:-change_me}"
JAVA_JAR="$ROOT/services/governor/target/gether-governor-0.1.0.jar"
WEB_ENV="$ROOT/services/web/.env.local"
PY="$ROOT/services/executor/.venv/Scripts/python.exe"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/services/executor/.venv/bin/python"
fi

log() { printf '[Gether] %s\n' "$*"; }
fail() { printf '[Gether] ERROR: %s\n' "$*" >&2; exit 1; }

docker_ok() {
  docker info >/dev/null 2>&1
}

ensure_docker() {
  if docker_ok; then return; fi
  log "Docker 未运行，尝试启动 Docker Desktop…"
  local exe="/c/Users/Lenovo/AppData/Local/Programs/DockerDesktop/Docker Desktop.exe"
  if [[ ! -f "$exe" ]]; then
    exe="/c/Program Files/Docker/Docker/Docker Desktop.exe"
  fi
  if [[ ! -f "$exe" ]]; then
    fail "找不到 Docker Desktop，请手动启动 Docker 后重试"
  fi
  powershell -NoProfile -Command "Start-Process '$exe'" >/dev/null 2>&1 || true
  for _ in $(seq 1 180); do
    if docker_ok; then log "Docker 已就绪"; return; fi
    sleep 2
  done
  fail "Docker 启动超时"
}

ensure_postgres() {
  local name
  for name in gether-postgres-local gether-postgres; do
    if docker inspect "$name" >/dev/null 2>&1; then
      docker start "$name" >/dev/null 2>&1 || true
      for _ in $(seq 1 30); do
        if docker exec "$name" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
          log "PostgreSQL 已就绪（$name:$POSTGRES_PORT）"
          return
        fi
        sleep 1
      done
      fail "PostgreSQL 容器未就绪：$name"
    fi
  done
  fail "找不到 gether-postgres-local / gether-postgres，请先执行 bash scripts/dev.sh up"
}

ensure_governor() {
  if docker inspect gether-gov-test >/dev/null 2>&1; then
    docker start gether-gov-test >/dev/null 2>&1 || true
  else
    [[ -f "$JAVA_JAR" ]] || fail "缺少 Java jar：$JAVA_JAR，请先构建 governor"
    log "创建 Java Governor 容器…"
    MSYS_NO_PATHCONV=1 docker run -d --name gether-gov-test \
      -p "${GOVERNOR_PORT}:8080" \
      -v "$JAVA_JAR:/app/app.jar:ro" \
      -e SPRING_DATASOURCE_URL="jdbc:postgresql://host.docker.internal:${POSTGRES_PORT}/${POSTGRES_DB}" \
      -e SPRING_DATASOURCE_USERNAME="$POSTGRES_USER" \
      -e SPRING_DATASOURCE_PASSWORD="$POSTGRES_PASSWORD" \
      -e EXECUTOR_BASE_URL="http://host.docker.internal:${EXECUTOR_PORT}" \
      -e CALLBACK_HMAC_SECRET="$HMAC_SECRET" \
      -e JWT_SECRET="$JWT_SECRET" \
      eclipse-temurin:21-jre java -jar /app/app.jar >/dev/null
  fi
  wait_http "http://localhost:${GOVERNOR_PORT}/api/health" "Java Governor" 90
}

ensure_python_env() {
  [[ -x "$PY" ]] || fail "缺少 Python 虚拟环境，请先执行 bash scripts/dev.sh install"
}

start_executor() {
  if curl -fsS "http://localhost:${EXECUTOR_PORT}/ready" >/dev/null 2>&1; then
    log "Python Executor 已在运行"
    return
  fi
  ensure_python_env
  log "启动 Python Executor…"
  (
    cd "$ROOT"
    nohup env POSTGRES_PORT="$POSTGRES_PORT" \
      GOVERNOR_BASE_URL="http://localhost:${GOVERNOR_PORT}" \
      HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}" \
      bash scripts/dev.sh executor \
      > "$LOG_DIR/executor.log" 2>&1 &
    echo $! > "$RUN_DIR/executor.pid"
  )
  wait_http "http://localhost:${EXECUTOR_PORT}/ready" "Python Executor" 90
}

start_web() {
  if curl -fsS "http://localhost:${WEB_PORT}" >/dev/null 2>&1; then
    log "前端已在运行"
    return
  fi
  [[ -d "$ROOT/services/web/node_modules" ]] || fail "缺少 node_modules，请先执行 bash scripts/dev.sh web-install"
  mkdir -p "$ROOT/services/web"
  if [[ ! -f "$WEB_ENV" ]]; then
    printf 'VITE_API_BASE_URL=http://localhost:%s\n' "$GOVERNOR_PORT" > "$WEB_ENV"
  fi
  log "启动前端…"
  (
    cd "$ROOT/services/web"
    VITE_API_BASE_URL="http://localhost:${GOVERNOR_PORT}"       nohup npm run dev -- --host 0.0.0.0 > "$LOG_DIR/web.log" 2>&1 &
    echo $! > "$RUN_DIR/web.pid"
  )
  wait_http "http://localhost:${WEB_PORT}" "前端" 60
}

wait_http() {
  local url="$1" name="$2" timeout="${3:-60}"
  for _ in $(seq 1 "$timeout"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$name 已就绪"
      return
    fi
    sleep 1
  done
  fail "$name 启动超时：$url（查看 $LOG_DIR）"
}

open_browser() {
  local url="http://localhost:${WEB_PORT}"
  log "打开 $url"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      powershell -NoProfile -Command "Start-Process '$url'" >/dev/null 2>&1 || true
      ;;
    Darwin*)
      open "$url" >/dev/null 2>&1 || true
      ;;
    *)
      xdg-open "$url" >/dev/null 2>&1 || true
      ;;
  esac
}

stop_host() {
  local file="$1" name="$2"
  if [[ ! -f "$file" ]]; then return; fi
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    taskkill //PID "$pid" //T //F >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
    log "已停止 $name"
  fi
  rm -f "$file"
}

stop_all() {
  stop_host "$RUN_DIR/web.pid" "前端"
  stop_host "$RUN_DIR/executor.pid" "Python Executor"
  docker stop gether-gov-test >/dev/null 2>&1 || true
  docker stop gether-postgres-local >/dev/null 2>&1 || true
  log "演示服务已停止（数据库容器已停止）"
}

cmd="${1:-start}"
case "$cmd" in
  start)
    ensure_docker
    ensure_postgres
    # 先拉起执行层，避免 Java 健康检查在启动窗口内把历史 PARSING 任务误判为失败。
    start_executor
    ensure_governor
    start_web
    open_browser
    log "演示环境就绪：前端 http://localhost:${WEB_PORT} | Java http://localhost:${GOVERNOR_PORT} | Python http://localhost:${EXECUTOR_PORT}"
    ;;
  status)
    echo "前端:   http://localhost:${WEB_PORT}"
    echo "Java:   http://localhost:${GOVERNOR_PORT}/api/health"
    echo "Python: http://localhost:${EXECUTOR_PORT}/ready"
    echo "日志:   $LOG_DIR"
    curl -fsS "http://localhost:${WEB_PORT}" >/dev/null 2>&1 && echo "前端: OK" || echo "前端: DOWN"
    curl -fsS "http://localhost:${GOVERNOR_PORT}/api/health" >/dev/null 2>&1 && echo "Java: OK" || echo "Java: DOWN"
    curl -fsS "http://localhost:${EXECUTOR_PORT}/ready" >/dev/null 2>&1 && echo "Python: OK" || echo "Python: DOWN"
    ;;
  stop)
    stop_all
    ;;
  logs)
    tail -n 80 "$LOG_DIR/executor.log" 2>/dev/null || true
    printf '\n--- web ---\n'
    tail -n 40 "$LOG_DIR/web.log" 2>/dev/null || true
    ;;
  *)
    echo "用法：bash scripts/start_demo.sh {start|status|stop|logs}"
    exit 2
    ;;
esac
