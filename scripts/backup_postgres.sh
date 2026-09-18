#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERRO: defina DATABASE_URL com o banco de produção." >&2
  exit 2
fi
if [[ -z "${BACKUP_DIR:-}" ]]; then
  echo "ERRO: defina BACKUP_DIR para um diretório protegido." >&2
  exit 2
fi
if ! command -v pg_dump >/dev/null 2>&1; then
  echo "ERRO: pg_dump não foi encontrado." >&2
  exit 2
fi

mkdir -p "$BACKUP_DIR"
umask 077
carimbo="$(date -u +%Y%m%dT%H%M%SZ)"
arquivo="$BACKUP_DIR/techfisco-licencas-$carimbo.dump"

pg_dump --format=custom --no-owner --no-privileges --file="$arquivo" "$DATABASE_URL"
shasum -a 256 "$arquivo" > "$arquivo.sha256"

echo "Backup criado: $arquivo"
echo "Checksum criado: $arquivo.sha256"
