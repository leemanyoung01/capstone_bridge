#!/usr/bin/env bash
# Docker 이미지 빌드 + 컨테이너 기동
set -euo pipefail

cd /opt/tastebridge

# .env 복구 (없으면 경고만)
if [ -f /tmp/tastebridge.env.bak ] && [ ! -f .env ]; then
  cp /tmp/tastebridge.env.bak .env
fi
if [ ! -f .env ]; then
  echo "⚠️  /opt/tastebridge/.env 가 없습니다. 최초 배포면 수동으로 생성 후 재배포 필요"
  exit 1
fi

docker compose -f docker-compose.deploy.yml up -d --build
echo "✅ started"