#!/usr/bin/env bash
# 在前台启动本机个人实例；密码不进入命令参数或日志。
set -euo pipefail
umask 077
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
password_file="$project_dir/backend/.local-password"
reset_password=0
if [[ "${1:-}" == "--set-password" ]]; then
  reset_password=1
  unset PAPER_PASSWORD
elif [[ $# -gt 0 ]]; then
  echo "用法：$0 [--set-password]" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo '请先安装 uv，再执行本脚本。' >&2
  exit 1
fi
if [[ ! -f "$project_dir/frontend/dist/index.html" ]]; then
  echo '请先在 frontend 目录执行 bun install --frozen-lockfile 和 bun run build。' >&2
  exit 1
fi
if [[ -z "${PAPER_PASSWORD:-}" ]]; then
  if [[ -f "$password_file" && "$reset_password" -eq 0 ]]; then
    PAPER_PASSWORD="$(cat "$password_file")"
  elif [[ -t 0 ]]; then
    read -r -s -p '设置个人登录密码（至少 12 字符，输入不显示）：' PAPER_PASSWORD
    echo
    read -r -s -p '再次输入密码：' password_confirmation
    echo
    if [[ "$PAPER_PASSWORD" != "$password_confirmation" ]]; then
      echo '两次密码不一致，未启动。' >&2
      exit 1
    fi
    if [[ ${#PAPER_PASSWORD} -lt 12 ]]; then
      echo '密码至少需要 12 字符，未启动。' >&2
      exit 1
    fi
    printf '%s' "$PAPER_PASSWORD" > "$password_file"
    unset password_confirmation
  else
    echo '首次启动请在交互终端执行此脚本以设置个人密码。' >&2
    exit 1
  fi
fi
if [[ ${#PAPER_PASSWORD} -lt 12 ]]; then
  echo '个人密码不足 12 字符，请更新本地密码配置。' >&2
  exit 1
fi
if [[ -f "$password_file" ]]; then chmod 600 "$password_file"; fi
export PAPER_PASSWORD
export PAPER_DB="$project_dir/backend/data/paper.sqlite3"
cd "$project_dir/backend"
exec uv run --frozen uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
