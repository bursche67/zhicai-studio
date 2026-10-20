#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null 2>&1; then
  echo '需要先安装Docker Engine和Compose插件。官方说明：https://docs.docker.com/engine/install/ubuntu/' >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo '请复制.env.example为.env，配置域名、访问账号和密码；不要将密钥上传GitHub。' >&2
  exit 1
fi
# Validate without printing interpolated secrets.
docker compose config --quiet
docker compose up -d --build
docker compose ps
