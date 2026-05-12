#!/usr/bin/env bash
# 덤프 파일 → 서버 DB로 복원
# 사용:   ./scripts/db_restore.sh dumps/tastebridge_xxx.sql
# 전제:   docker-compose.deploy.yml 의 db 컨테이너가 떠있고
#         .env 의 POSTGRES_USER/POSTGRES_DB 와 일치해야 함
set -euo pipefail

DUMP="${1:?usage: $0 <dump.sql>}"
[ -f "$DUMP" ] || { echo "❌ not found: $DUMP"; exit 1; }

CONTAINER="${DB_CONTAINER:-tastebridge_db}"
DB_USER="${POSTGRES_USER:-tastebridge}"
DB_NAME="${POSTGRES_DB:-tastebridge}"

echo "→ restoring $DUMP → ${CONTAINER}:${DB_NAME}"
docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" < "$DUMP"
echo "✅ restore complete"