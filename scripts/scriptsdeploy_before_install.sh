#!/usr/bin/env bash
# 이전 코드 정리 (단, .env와 db 볼륨은 절대 건드리지 않음)
set -euo pipefail

TARGET=/opt/tastebridge
mkdir -p "$TARGET"

# .env 백업 → 새 코드 들어온 뒤 복구
if [ -f "$TARGET/.env" ]; then
  cp "$TARGET/.env" /tmp/tastebridge.env.bak
fi

# 코드 파일만 삭제 (.env, dumps/, postgres volume은 docker가 관리하니 OK)
find "$TARGET" -mindepth 1 -maxdepth 1 \
  ! -name '.env' \
  ! -name 'dumps' \
  -exec rm -rf {} +

echo "✅ before_install done"