#!/usr/bin/env bash
# Gether 本地开发脚本（Windows Git Bash / Linux / macOS 通用）
set -euo pipefail

# 将模型/包缓存固定到 D 盘，避免 C 盘被占满（Windows Git Bash）
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    export HF_HOME="${HF_HOME:-D:/DevCache/huggingface}"
    export PIP_CACHE_DIR="${PIP_CACHE_DIR:-D:/DevCache/pip}"
    export NPM_CONFIG_CACHE="${NPM_CONFIG_CACHE:-D:/DevCache/npm-cache}"
    export FASTEMBED_CACHE_PATH="${FASTEMBED_CACHE_PATH:-D:/DevCache/fastembed}"
    ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXECUTOR="$ROOT/services/executor"
GOVERNOR="$ROOT/services/governor"
WEB="$ROOT/services/web"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/infra/docker-compose.yml")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose --env-file "$ROOT/.env" -f "$ROOT/infra/docker-compose.yml")
else
  echo "未找到 docker compose / docker-compose" >&2
  exit 1
fi

if [[ -x "$EXECUTOR/.venv/Scripts/python.exe" ]]; then
  PY="$EXECUTOR/.venv/Scripts/python.exe"
elif [[ -x "$EXECUTOR/.venv/bin/python" ]]; then
  PY="$EXECUTOR/.venv/bin/python"
else
  PY=""
fi

ensure_python() {
  if [[ -z "$PY" ]]; then
    echo "未找到虚拟环境，请先执行：bash scripts/dev.sh install" >&2
    exit 1
  fi
}

ensure_env() {
  if [[ ! -f "$ROOT/.env" ]]; then
    echo "未找到 .env，已从 infra/.env.example 复制。请填写 LLM Key，或将 LLM_PROVIDER 改为 mock。" >&2
    cp "$ROOT/infra/.env.example" "$ROOT/.env"
  fi
}

cmd="${1:-help}"

case "$cmd" in
  init)
    ensure_env
    echo "已准备 .env：$ROOT/.env"
    ;;

  up)
    ensure_env
    "${COMPOSE[@]}" up -d postgres minio redis
    echo "Postgres :5432 | MinIO :9000 (console :9001) | Redis :6379"
    ;;

  down)
    "${COMPOSE[@]}" down
    ;;

  ps)
    "${COMPOSE[@]}" ps
    ;;

  logs)
    "${COMPOSE[@]}" logs -f --tail=100 "${@:2}"
    ;;

  install)
    cd "$EXECUTOR"
    [[ -d .venv ]] || python -m venv .venv
    if [[ -x .venv/Scripts/python.exe ]]; then PY=".venv/Scripts/python.exe"; else PY=".venv/bin/python"; fi
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -e ".[dev]" \
      -i "${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}" --timeout 60
    echo "Python 依赖安装完成"
    ;;

  executor)
    ensure_python
    cd "$EXECUTOR"
    exec "$PY" scripts/run.py
    ;;

  test)
    ensure_python
    cd "$EXECUTOR"
    exec "$PY" -m pytest -q
    ;;

  smoke)
    ensure_python
    cd "$EXECUTOR"
    LLM_PROVIDER=mock APP_ENV=local "$PY" -m pytest -q
    ;;

  lint)
    ensure_python
    cd "$EXECUTOR"
    exec "$PY" -m ruff check app tests
    ;;

  check-llm)
    ensure_python
    cd "$EXECUTOR"
    exec "$PY" scripts/check_llm.py
    ;;

  demo)
    ensure_python
    exec "$PY" "$ROOT/scripts/demo_parse.py"
    ;;

  export)
    ensure_python
    cd "$EXECUTOR"
    exec "$PY" "$ROOT/scripts/export_evidence.py" "${@:2}"
    ;;

  fuse)
    ensure_python
    exec "$PY" "$ROOT/scripts/fuse_export.py" "${@:2}"
    ;;

  eval-retrieval)
    ensure_python
    exec "$PY" "$ROOT/eval/scripts/tune_retrieval.py" "${@:2}"
    ;;

  web-install)
    cd "$WEB"
    npm install --registry="${NPM_REGISTRY:-https://registry.npmmirror.com}"
    ;;

  web)
    cd "$WEB"
    exec npm run dev
    ;;

  governor)
    if command -v mvn >/dev/null 2>&1; then
      cd "$GOVERNOR"
      exec mvn spring-boot:run
    elif command -v docker >/dev/null 2>&1; then
      ensure_env
      "${COMPOSE[@]}" --profile app up -d governor
      echo "Governor 通过 Docker 启动：http://localhost:8080"
    else
      echo "未安装 Maven 或 Docker。请安装 JDK 21 + Maven，或使用 Docker Compose。" >&2
      exit 1
    fi
    ;;

  build)
    ensure_env
    "${COMPOSE[@]}" --profile app build
    ;;

  help|*)
    cat <<'EOF'
Gether 开发命令
  init          复制配置模板到 .env
  up / down     启动/停止 Postgres + MinIO + Redis
  ps / logs     查看容器状态 / 日志
  install       创建 venv 并安装执行层依赖
  executor      启动 FastAPI（:8000）
  test          运行测试
  smoke         使用 mock LLM 的离线冒烟测试
  lint          ruff 检查
  check-llm     检查当前 LLM 供应商连通性
  demo          本地 fixture 演示：创建任务→解析→查证据（需先启动 executor）
  export        解析单个视频并导出 evidence.json / .md / .csv
  fuse          对已有 evidence.json 重做 OCR/ASR 融合（不重新下载）
  eval-retrieval 混合检索网格搜索（可加 --round 1/2/3）
  web-install   安装前端依赖
  web           启动前端（:5173）
  governor      启动 Java 治理层（本机 Maven 或 Docker）
  build         构建应用镜像
EOF
    ;;
esac
