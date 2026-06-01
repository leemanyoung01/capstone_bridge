#!/usr/bin/env bash
# 로컬 DB → 덤프 파일 생성
# 사용:   ./scripts/db_dump.sh [output_path]
# 예시:   ./scripts/db_dump.sh dumps/tastebridge_$(date +%Y%m%d).sql
set -euo pipefail

OUT="${1:-dumps/tastebridge_$(date +%Y%m%d_%H%M%S).sql}"
mkdir -p "$(dirname "$OUT")"

: "${DATABASE_URL:?DATABASE_URL not set (예: postgresql://user:pw@host:5432/db)}"

echo "→ dumping $DATABASE_URL → $OUT"
pg_dump \
  --no-owner \
  --no-privileges \
  --clean \
  --if-exists \
  --format=plain \
  "$DATABASE_URL" > "$OUT"

echo "✅ done: $(du -h "$OUT" | cut -f1)  $OUT"