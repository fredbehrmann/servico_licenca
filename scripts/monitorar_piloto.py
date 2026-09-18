#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monitora o piloto do licenciamento sem coletar dado fiscal ou pessoal.

O monitor consulta somente saúde, prontidão, ambiente e relatórios administrativos
agregados. O token é lido de ``ADMIN_TOKEN`` e nunca é aceito pela linha de
comando, gravado em arquivo ou incluído em mensagens de erro.

Exemplo para um ciclo de sete dias, com amostra a cada cinco minutos:

    export ADMIN_TOKEN='segredo-no-cofre'
    export PILOTO_OPERADOR='fred'
    python scripts/monitorar_piloto.py \
      --servico-url https://licenca.techfisco.com.br \
      --sicof-url https://app-piloto.exemplo.gov.br \
      --saida /diretorio-protegido/piloto \
      --duracao-horas 168 --intervalo-segundos 300
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_ROTAS_ADMIN_CONHECIDAS = frozenset({
    "/admin/licencas",
    "/admin/licencas/{id}",
    "/admin/licencas/{id}/instalacoes",
    "/admin/revogar",
    "/admin/revogar-municipio",
    "/admin/reativar-municipio",
    "/admin/autorizar",
    "/admin/auditoria",
    "/admin/tentativas",
    "/admin/instalacoes",
})


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(data: datetime) -> str:
    return data.astimezone(timezone.utc).isoformat(timespec="seconds")


def normalizar_url(url: str, *, permitir_http_local: bool = False) -> str:
    texto = (url or "").strip().rstrip("/")
    partes = urllib.parse.urlsplit(texto)
    local = partes.hostname in {"127.0.0.1", "localhost", "::1"}
    esquema_valido = partes.scheme == "https" or (
        permitir_http_local and partes.scheme == "http" and local
    )
    if not esquema_valido or not partes.hostname or partes.path not in {"", "/"}:
        raise ValueError(
            "informe somente a origem HTTPS, sem caminho; HTTP é permitido apenas em localhost"
        )
    if partes.username or partes.password or partes.query or partes.fragment:
        raise ValueError("a URL não pode conter credencial, consulta ou fragmento")
    return texto


def _requisitar_json(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, dict[str, Any], int]:
    inicio = time.monotonic()
    requisicao = urllib.request.Request(url, headers=headers, method="GET")
    contexto = ssl.create_default_context()
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout, context=contexto) as resposta:
            status = int(resposta.status)
            bruto = resposta.read(512_000)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        bruto = exc.read(512_000)
    latencia_ms = round((time.monotonic() - inicio) * 1000)
    try:
        corpo = json.loads(bruto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        corpo = {}
    return status, corpo if isinstance(corpo, dict) else {}, latencia_ms


def _contar_status(registros: list[dict[str, Any]], campo: str) -> dict[str, int]:
    permitidos = {
        "ativa", "expirada", "revogada", "suspensa", "pendente",
        "instalacao_nao_autorizada", "erro_temporario",
        "200", "201", "202", "204", "400", "401", "403", "404",
        "409", "422", "429", "500", "502", "503", "504",
    }
    contagem: Counter[str] = Counter()
    for registro in registros:
        valor = str(registro.get(campo) or "desconhecido").strip().lower()
        contagem[valor if valor in permitidos else "outro"] += 1
    return dict(sorted(contagem.items()))


def _rota_agregada(valor: Any) -> str:
    rota = _UUID.sub("{id}", str(valor or ""))
    return rota if rota in _ROTAS_ADMIN_CONHECIDAS else "outra_rota_admin"


def _parse_iso(valor: Any) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return data if data.tzinfo else data.replace(tzinfo=timezone.utc)


def resumir_admin(
    licencas: list[dict[str, Any]],
    tentativas: list[dict[str, Any]],
    auditoria: list[dict[str, Any]],
    *,
    agora: datetime,
    dias_alerta_expiracao: int,
) -> dict[str, Any]:
    """Agrega métricas e descarta IBGE, instalação, licença e operador."""
    corte = agora + timedelta(days=dias_alerta_expiracao)
    expirando = 0
    for licenca in licencas:
        fim = _parse_iso(licenca.get("expira_em"))
        if fim is not None and agora <= fim <= corte:
            expirando += 1

    acoes: Counter[str] = Counter()
    for evento in auditoria:
        metodo = str(evento.get("acao") or "?").upper()
        acoes[f"{metodo} {_rota_agregada(evento.get('alvo'))}"] += 1

    return {
        "licencas_total": len(licencas),
        "licencas_por_status": _contar_status(licencas, "status"),
        "licencas_expirando": expirando,
        "consultas_total_na_janela": len(tentativas),
        "consultas_por_status": _contar_status(tentativas, "status"),
        "acoes_admin_total_na_janela": len(auditoria),
        "acoes_admin_por_resultado": _contar_status(auditoria, "resultado"),
        "acoes_admin_por_rota": dict(sorted(acoes.items())),
    }


def _obter(
    requisitar: Callable[[str, dict[str, str], float], tuple[int, dict[str, Any], int]],
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    try:
        status, corpo, latencia = requisitar(url, headers, timeout)
        return {"status_http": status, "latencia_ms": latencia, "corpo": corpo}
    except Exception as exc:
        # O texto da exceção pode conter URL, proxy ou detalhe local. Só o tipo é
        # suficiente para classificar a indisponibilidade sem vazar configuração.
        return {
            "status_http": 0,
            "latencia_ms": None,
            "corpo": {},
            "erro": type(exc).__name__,
        }


def coletar_amostra(
    servico_url: str,
    sicof_url: str,
    *,
    token_admin: str,
    operador: str,
    latencia_alerta_ms: int,
    dias_alerta_expiracao: int,
    timeout: float = 15,
    requisitar: Callable[
        [str, dict[str, str], float], tuple[int, dict[str, Any], int]
    ] = _requisitar_json,
    agora: datetime | None = None,
) -> dict[str, Any]:
    agora = agora or _agora()
    alertas: list[dict[str, str]] = []
    publico: dict[str, Any] = {}

    for nome, url in (
        ("servico_health", f"{servico_url}/health"),
        ("servico_ready", f"{servico_url}/ready"),
        ("sicof_health", f"{sicof_url}/health"),
        ("sicof_ready", f"{sicof_url}/ready"),
        ("sicof_ambiente", f"{sicof_url}/ambiente"),
    ):
        resposta = _obter(requisitar, url, {}, timeout)
        corpo = resposta.pop("corpo")
        publico[nome] = {
            **resposta,
            "ok": resposta["status_http"] == 200 and corpo.get("ok", True) is not False,
        }
        if nome == "sicof_ambiente":
            publico[nome].update({
                "ambiente": str(corpo.get("ambiente") or "desconhecido"),
                "controles_ativos": bool(corpo.get("controles_ativos")),
                "configuracao_valida": bool(corpo.get("configuracao_valida")),
            })

    for nome in ("servico_health", "servico_ready", "sicof_health", "sicof_ready"):
        medicao = publico[nome]
        if not medicao["ok"]:
            alertas.append({
                "severidade": "critica",
                "codigo": f"{nome}_indisponivel",
            })
        latencia = medicao.get("latencia_ms")
        if isinstance(latencia, int) and latencia > latencia_alerta_ms:
            alertas.append({
                "severidade": "aviso",
                "codigo": f"{nome}_latencia_alta",
            })

    ambiente = publico["sicof_ambiente"]
    if ambiente.get("ambiente") != "producao" or not ambiente.get("controles_ativos"):
        alertas.append({"severidade": "critica", "codigo": "sicof_nao_produtivo"})

    admin_resumo: dict[str, Any] = {"coletado": False}
    if token_admin:
        headers = {
            "Authorization": f"Bearer {token_admin}",
            "X-Admin-Operador": operador,
        }
        respostas = {
            "licencas": _obter(requisitar, f"{servico_url}/admin/licencas", headers, timeout),
            "tentativas": _obter(
                requisitar, f"{servico_url}/admin/tentativas?limite=500", headers, timeout
            ),
            "auditoria": _obter(
                requisitar, f"{servico_url}/admin/auditoria?limite=500", headers, timeout
            ),
        }
        if all(item["status_http"] == 200 for item in respostas.values()):
            admin_resumo = {
                "coletado": True,
                **resumir_admin(
                    respostas["licencas"]["corpo"].get("licencas", []),
                    respostas["tentativas"]["corpo"].get("tentativas", []),
                    respostas["auditoria"]["corpo"].get("eventos", []),
                    agora=agora,
                    dias_alerta_expiracao=dias_alerta_expiracao,
                ),
            }
            if admin_resumo["licencas_expirando"]:
                alertas.append({
                    "severidade": "aviso", "codigo": "licenca_proxima_do_fim",
                })
            resultados = admin_resumo["acoes_admin_por_resultado"]
            if any(resultados.get(codigo, 0) for codigo in ("500", "502", "503", "504")):
                alertas.append({
                    "severidade": "critica", "codigo": "falha_administrativa_5xx",
                })
        else:
            alertas.append({
                "severidade": "critica", "codigo": "relatorios_admin_indisponiveis",
            })
    else:
        alertas.append({
            "severidade": "critica", "codigo": "monitoramento_admin_sem_token",
        })

    return {
        "quando": _iso(agora),
        "publico": publico,
        "administracao": admin_resumo,
        "alertas": alertas,
        "possui_alerta_critico": any(a["severidade"] == "critica" for a in alertas),
    }


def resumir_amostras(amostras: list[dict[str, Any]]) -> dict[str, Any]:
    alertas: Counter[str] = Counter()
    criticas = 0
    latencias: list[int] = []
    for amostra in amostras:
        criticas += int(bool(amostra.get("possui_alerta_critico")))
        for alerta in amostra.get("alertas", []):
            alertas[str(alerta.get("codigo") or "desconhecido")] += 1
        for medicao in amostra.get("publico", {}).values():
            valor = medicao.get("latencia_ms")
            if isinstance(valor, int):
                latencias.append(valor)
    return {
        "amostras": len(amostras),
        "amostras_com_alerta_critico": criticas,
        "alertas_por_codigo": dict(sorted(alertas.items())),
        "latencia_maxima_ms": max(latencias) if latencias else None,
        "resultado": "interromper" if criticas else "acompanhar",
    }


def _gravar_json(path: Path, valor: Any) -> None:
    path.write_text(
        json.dumps(valor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)


def executar(args: argparse.Namespace) -> int:
    servico_url = normalizar_url(
        args.servico_url, permitir_http_local=args.permitir_http_local
    )
    sicof_url = normalizar_url(args.sicof_url, permitir_http_local=args.permitir_http_local)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    operador = os.environ.get("PILOTO_OPERADOR", "monitor-piloto").strip()
    if token and len(operador) < 3:
        raise ValueError("PILOTO_OPERADOR deve identificar o responsável")

    saida = Path(args.saida).expanduser().resolve()
    saida.mkdir(parents=True, exist_ok=True)
    saida.chmod(0o700)
    arquivo_amostras = saida / "amostras.ndjson"
    inicio = time.monotonic()
    duracao_s = max(0.0, args.duracao_horas * 3600)
    amostras: list[dict[str, Any]] = []

    while True:
        amostra = coletar_amostra(
            servico_url,
            sicof_url,
            token_admin=token,
            operador=operador,
            latencia_alerta_ms=args.latencia_alerta_ms,
            dias_alerta_expiracao=args.dias_alerta_expiracao,
            timeout=args.timeout_segundos,
        )
        amostras.append(amostra)
        with arquivo_amostras.open("a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(amostra, ensure_ascii=False) + "\n")
        arquivo_amostras.chmod(0o600)
        _gravar_json(saida / "resumo.json", resumir_amostras(amostras))

        if args.uma_vez or time.monotonic() - inicio >= duracao_s:
            break
        restante = max(0.0, duracao_s - (time.monotonic() - inicio))
        time.sleep(min(float(args.intervalo_segundos), restante))

    resumo = resumir_amostras(amostras)
    print(json.dumps(resumo, ensure_ascii=False, sort_keys=True))
    return 2 if resumo["amostras_com_alerta_critico"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servico-url", required=True)
    parser.add_argument("--sicof-url", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--duracao-horas", type=float, default=168)
    parser.add_argument("--intervalo-segundos", type=int, default=300)
    parser.add_argument("--latencia-alerta-ms", type=int, default=2000)
    parser.add_argument("--dias-alerta-expiracao", type=int, default=30)
    parser.add_argument("--timeout-segundos", type=float, default=15)
    parser.add_argument("--uma-vez", action="store_true")
    parser.add_argument(
        "--permitir-http-local", action="store_true",
        help="somente para ensaio em localhost; produção continua exigindo HTTPS",
    )
    args = parser.parse_args()
    if args.intervalo_segundos < 10 and not args.uma_vez:
        parser.error("intervalo-segundos deve ser pelo menos 10")
    if args.timeout_segundos <= 0 or args.latencia_alerta_ms <= 0:
        parser.error("timeouts e limites devem ser positivos")
    return executar(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(2)
