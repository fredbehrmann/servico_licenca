# -*- coding: utf-8 -*-
"""Modelo administrativo da licença contratual.

O contrato comercial e a instalação são conceitos diferentes. Este módulo
normaliza os dados administrativos sem conhecer HTTP nem banco. A resposta
assinada ao SICOF continua no contrato v1; estados internos mais detalhados são
traduzidos por :mod:`app.servico`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


STATUS_LICENCA = frozenset({"ativa", "suspensa", "revogada", "expirada", "pendente"})
STATUS_INSTALACAO = frozenset({"ativa", "contingencia", "revogada", "substituida"})


class LicencaInvalida(ValueError):
    """Dados administrativos não formam uma licença coerente."""


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_data(valor: Any, campo: str) -> datetime:
    texto = str(valor or "").strip()
    if not texto:
        raise LicencaInvalida(f"{campo} é obrigatório")
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicencaInvalida(f"{campo} deve usar data/hora ISO 8601") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(valor: datetime) -> str:
    return valor.astimezone(timezone.utc).isoformat()


def _inteiro_positivo(valor: Any, campo: str, padrao: Optional[int] = None) -> int:
    if valor in (None, "") and padrao is not None:
        return padrao
    try:
        numero = int(valor)
    except (TypeError, ValueError) as exc:
        raise LicencaInvalida(f"{campo} deve ser um número inteiro") from exc
    if numero < 1:
        raise LicencaInvalida(f"{campo} deve ser maior que zero")
    return numero


def normalizar_criacao(corpo: dict[str, Any], agora: Optional[datetime] = None) -> dict[str, Any]:
    agora = (agora or agora_utc()).astimezone(timezone.utc)
    ibge = str(corpo.get("codigo_ibge") or "").strip()
    if not ibge:
        raise LicencaInvalida("codigo_ibge é obrigatório")
    inicio = parse_data(corpo.get("inicio_em"), "inicio_em")
    fim = parse_data(corpo.get("expira_em"), "expira_em")
    if fim <= inicio:
        raise LicencaInvalida("expira_em deve ser posterior a inicio_em")
    status = str(corpo.get("status") or "pendente").strip().lower()
    if status not in STATUS_LICENCA:
        raise LicencaInvalida(f"status deve ser um de {sorted(STATUS_LICENCA)}")
    return {
        "licenca_id": str(corpo.get("licenca_id") or uuid4()),
        "codigo_ibge": ibge,
        "status": status,
        "inicio_em": iso(inicio),
        "expira_em": iso(fim),
        "max_auditores": _inteiro_positivo(corpo.get("max_auditores"), "max_auditores"),
        "max_instalacoes_ativas": _inteiro_positivo(
            corpo.get("max_instalacoes_ativas"), "max_instalacoes_ativas", 1
        ),
        "dias_offline": _inteiro_positivo(corpo.get("dias_offline"), "dias_offline", 7),
        "versao_minima": str(corpo.get("versao_minima") or "1.0.0").strip(),
        "criada_em": iso(agora),
        "atualizada_em": iso(agora),
    }


def normalizar_atualizacao(
    existente: dict[str, Any], corpo: dict[str, Any], agora: Optional[datetime] = None
) -> dict[str, Any]:
    if not existente:
        raise LicencaInvalida("licença não encontrada")
    proibidos = {"licenca_id", "codigo_ibge", "criada_em"}.intersection(corpo)
    if proibidos:
        raise LicencaInvalida(f"campos imutáveis: {sorted(proibidos)}")
    mesclado = dict(existente)
    for campo in (
        "status", "inicio_em", "expira_em", "max_auditores",
        "max_instalacoes_ativas", "dias_offline", "versao_minima",
    ):
        if campo in corpo:
            mesclado[campo] = corpo[campo]
    # Reutiliza a validação de criação sem trocar identidade/município.
    validado = normalizar_criacao({
        **mesclado,
        "licenca_id": existente["licenca_id"],
        "codigo_ibge": existente["codigo_ibge"],
    }, agora=agora)
    validado["criada_em"] = str(existente.get("criada_em") or validado["criada_em"])
    return validado


def normalizar_instalacao(corpo: dict[str, Any]) -> dict[str, str]:
    instalacao_id = str(corpo.get("instalacao_id") or "").strip()
    if not instalacao_id:
        raise LicencaInvalida("instalacao_id é obrigatório")
    status = str(corpo.get("status") or "ativa").strip().lower()
    if status not in STATUS_INSTALACAO:
        raise LicencaInvalida(f"status deve ser um de {sorted(STATUS_INSTALACAO)}")
    return {"instalacao_id": instalacao_id, "status": status}
