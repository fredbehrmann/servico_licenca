#!/usr/bin/env bash
set -euo pipefail

arquivo="${1:-}"
if [[ -z "$arquivo" || ! -f "$arquivo" ]]; then
  echo "Uso: RESTORE_DATABASE_URL=... CONFIRMAR_RESTAURACAO_TESTE=SIM $0 arquivo.dump" >&2
  exit 2
fi
if [[ -z "${RESTORE_DATABASE_URL:-}" ]]; then
  echo "ERRO: defina RESTORE_DATABASE_URL com um banco descartável de teste." >&2
  exit 2
fi
if [[ "${RESTORE_DATABASE_URL}" == "${DATABASE_URL:-}" ]]; then
  echo "ERRO: o banco de restauração não pode ser o banco de origem/produção." >&2
  exit 2
fi
if [[ "${CONFIRMAR_RESTAURACAO_TESTE:-}" != "SIM" ]]; then
  echo "ERRO: defina CONFIRMAR_RESTAURACAO_TESTE=SIM após conferir o banco descartável." >&2
  exit 2
fi
if ! command -v pg_restore >/dev/null 2>&1 || ! command -v psql >/dev/null 2>&1; then
  echo "ERRO: pg_restore e psql são necessários." >&2
  exit 2
fi

if [[ -f "$arquivo.sha256" ]]; then
  shasum -a 256 -c "$arquivo.sha256"
fi

pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname="$RESTORE_DATABASE_URL" "$arquivo"

psql "$RESTORE_DATABASE_URL" -v ON_ERROR_STOP=1 -c \
  "SELECT versao, nome, aplicada_em FROM migracoes_schema ORDER BY versao;"
psql "$RESTORE_DATABASE_URL" -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) AS licencas FROM licencas;"

echo "Restauração de teste concluída. Registre data, responsável e arquivo no checklist operacional."
