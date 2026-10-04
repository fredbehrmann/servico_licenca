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

from app.validacao import (
    MAX_AUDITORES,
    MAX_DIAS_OFFLINE,
    MAX_INSTALACOES_ATIVAS,
    EntradaInvalida,
    codigo_ibge,
    inteiro,
    uuid_canonico,
    validar_campos,
    versao,
)


STATUS_LICENCA = frozenset({"ativa", "suspensa", "revogada", "expirada", "pendente"})
STATUS_INSTALACAO = frozenset({"ativa", "contingencia", "revogada", "substituida"})
CAMPOS_CRIACAO = frozenset({
    "licenca_id", "codigo_ibge", "nome_municipio", "status", "inicio_em", "expira_em",
    "max_auditores", "max_instalacoes_ativas", "dias_offline", "versao_minima",
})
CAMPOS_ATUALIZACAO = frozenset({
    "nome_municipio", "status", "inicio_em", "expira_em", "max_auditores",
    "max_instalacoes_ativas", "dias_offline", "versao_minima",
})


class LicencaInvalida(ValueError):
    """Dados administrativos não formam uma licença coerente."""


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_data(valor: Any, campo: str) -> datetime:
    if not isinstance(valor, str):
        raise LicencaInvalida(f"{campo} deve usar data/hora ISO 8601 com fuso horário")
    texto = valor.strip()
    if texto != valor:
        raise LicencaInvalida(f"{campo} não pode conter espaços nas extremidades")
    if not texto:
        raise LicencaInvalida(f"{campo} é obrigatório")
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicencaInvalida(f"{campo} deve usar data/hora ISO 8601") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise LicencaInvalida(f"{campo} deve informar o fuso horário")
    return dt.astimezone(timezone.utc)


def iso(valor: datetime) -> str:
    return valor.astimezone(timezone.utc).isoformat()


def _inteiro_positivo(valor: Any, campo: str, *, maximo: int) -> int:
    try:
        numero = inteiro(valor, campo, maximo=maximo)
    except EntradaInvalida as exc:
        raise LicencaInvalida(str(exc)) from exc
    assert numero is not None
    return numero


def _nome_municipio(valor: Any) -> str:
    if valor in (None, ""):
        return ""
    if not isinstance(valor, str):
        raise LicencaInvalida("nome_municipio deve ser texto")
    if any(ord(caractere) < 32 or ord(caractere) == 127 for caractere in valor):
        raise LicencaInvalida("nome_municipio contém caracteres de controle")
    nome = " ".join(valor.strip().split())
    if len(nome) > 160:
        raise LicencaInvalida("nome_municipio deve ter no máximo 160 caracteres")
    return nome


def normalizar_criacao(corpo: dict[str, Any], agora: Optional[datetime] = None) -> dict[str, Any]:
    try:
        validar_campos(
            corpo, CAMPOS_CRIACAO,
            obrigatorios={"codigo_ibge", "inicio_em", "expira_em", "max_auditores"},
        )
    except EntradaInvalida as exc:
        raise LicencaInvalida(str(exc)) from exc
    agora = (agora or agora_utc()).astimezone(timezone.utc)
    try:
        ibge = codigo_ibge(corpo.get("codigo_ibge"))
    except EntradaInvalida as exc:
        raise LicencaInvalida(str(exc)) from exc
    inicio = parse_data(corpo.get("inicio_em"), "inicio_em")
    fim = parse_data(corpo.get("expira_em"), "expira_em")
    if fim <= inicio:
        raise LicencaInvalida("expira_em deve ser posterior a inicio_em")
    status_bruto = corpo["status"] if "status" in corpo else "pendente"
    if not isinstance(status_bruto, str):
        raise LicencaInvalida("status deve ser texto")
    status = status_bruto.strip().lower()
    if status not in STATUS_LICENCA:
        raise LicencaInvalida(f"status deve ser um de {sorted(STATUS_LICENCA)}")
    return {
        "licenca_id": str(corpo.get("licenca_id") or uuid4()),
        "codigo_ibge": ibge,
        "nome_municipio": _nome_municipio(corpo.get("nome_municipio")),
        "status": status,
        "inicio_em": iso(inicio),
        "expira_em": iso(fim),
        "max_auditores": _inteiro_positivo(
            corpo.get("max_auditores"), "max_auditores", maximo=MAX_AUDITORES
        ),
        "max_instalacoes_ativas": _inteiro_positivo(
            corpo["max_instalacoes_ativas"] if "max_instalacoes_ativas" in corpo else 1,
            "max_instalacoes_ativas",
            maximo=MAX_INSTALACOES_ATIVAS,
        ),
        "dias_offline": _inteiro_positivo(
            corpo["dias_offline"] if "dias_offline" in corpo else 7,
            "dias_offline", maximo=MAX_DIAS_OFFLINE,
        ),
        "versao_minima": _versao_minima(corpo.get("versao_minima", "1.0.0")),
        "criada_em": iso(agora),
        "atualizada_em": iso(agora),
    }


def normalizar_atualizacao(
    existente: dict[str, Any], corpo: dict[str, Any], agora: Optional[datetime] = None,
    *, confirmar_encurtamento: bool = False,
) -> dict[str, Any]:
    if not existente:
        raise LicencaInvalida("licença não encontrada")
    proibidos = {"licenca_id", "codigo_ibge", "criada_em", "atualizada_em"}.intersection(corpo)
    if proibidos:
        raise LicencaInvalida(f"campos imutáveis: {sorted(proibidos)}")
    try:
        validar_campos(corpo, CAMPOS_ATUALIZACAO)
    except EntradaInvalida as exc:
        raise LicencaInvalida(str(exc)) from exc
    if not corpo:
        raise LicencaInvalida("informe ao menos um campo para atualizar")
    if "expira_em" in corpo:
        novo_fim = parse_data(corpo["expira_em"], "expira_em")
        if existente.get("expira_em"):
            fim_atual = parse_data(existente.get("expira_em"), "expira_em atual")
            if novo_fim < fim_atual and not confirmar_encurtamento:
                raise LicencaInvalida(
                    "reduzir expira_em exige confirmar_encurtamento_vigencia=true"
                )
    mesclado = {
        campo: existente.get(campo)
        for campo in CAMPOS_CRIACAO
        if campo not in {"licenca_id", "codigo_ibge"}
    }
    mesclado.update(corpo)
    # Reutiliza a validação de criação sem trocar identidade/município.
    validado = normalizar_criacao({
        **mesclado,
        "licenca_id": existente["licenca_id"],
        "codigo_ibge": existente["codigo_ibge"],
    }, agora=agora)
    validado["criada_em"] = str(existente.get("criada_em") or validado["criada_em"])
    return validado


def normalizar_instalacao(corpo: dict[str, Any]) -> dict[str, Any]:
    try:
        validar_campos(
            corpo,
            {"instalacao_id", "status", "confirmar_transferencia"},
            obrigatorios={"instalacao_id"},
        )
        instalacao_id = uuid_canonico(corpo.get("instalacao_id"))
    except EntradaInvalida as exc:
        raise LicencaInvalida(str(exc)) from exc
    status_bruto = corpo["status"] if "status" in corpo else "ativa"
    if not isinstance(status_bruto, str):
        raise LicencaInvalida("status deve ser texto")
    status = status_bruto.strip().lower()
    if status not in STATUS_INSTALACAO:
        raise LicencaInvalida(f"status deve ser um de {sorted(STATUS_INSTALACAO)}")
    confirmar = corpo.get("confirmar_transferencia", False)
    if not isinstance(confirmar, bool):
        raise LicencaInvalida("confirmar_transferencia deve ser verdadeiro ou falso")
    return {
        "instalacao_id": instalacao_id,
        "status": status,
        "confirmar_transferencia": confirmar,
    }


def _versao_minima(valor: Any) -> str:
    try:
        return versao(valor, "versao_minima")
    except EntradaInvalida as exc:
        raise LicencaInvalida(str(exc)) from exc
