#!/usr/bin/env bash
set -euo pipefail

url="${1:-}"
if [[ ! "$url" =~ ^https://[^/]+$ ]]; then
  echo "Uso: $0 https://dominio-sem-barra-final" >&2
  exit 2
fi

echo "Verificando processo em $url/health"
health="$(curl --fail --silent --show-error --max-time 15 "$url/health")"
[[ "$health" == '{"ok":true}' ]] || {
  echo "ERRO: /health não devolveu o contrato mínimo esperado." >&2
  exit 1
}

echo "Verificando prontidão em $url/ready"
ready="$(curl --fail --silent --show-error --max-time 15 "$url/ready")"
[[ "$ready" == '{"ok":true}' ]] || {
  echo "ERRO: /ready não confirmou a prontidão." >&2
  exit 1
}

host="${url#https://}"
fim_certificado="$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null \
  | openssl x509 -noout -enddate)"
echo "HTTPS válido; $fim_certificado"
