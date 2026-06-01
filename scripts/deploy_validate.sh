#!/usr/bin/env bash
# 헬스체크: /api/health 응답 확인
set -euo pipefail

for i in {1..20}; do
  if curl -sf http://localhost/api/health > /dev/null; then
    echo "✅ healthy"
    exit 0
  fi
  echo "  waiting... ($i/20)"
  sleep 3
done

echo "❌ health check failed"
docker compose -f /opt/tastebridge/docker-compose.deploy.yml logs --tail=50 app || true
exit 1