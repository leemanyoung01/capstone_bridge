#!/usr/bin/env bash
# 이전 버전 중지 (있으면)
set -euo pipefail
cd /opt/tastebridge || exit 0
if [ -f docker-compose.deploy.yml ]; then
  docker compose -f docker-compose.deploy.yml down || true
fi
echo "✅ stopped"