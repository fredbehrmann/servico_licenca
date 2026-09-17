# -*- coding: utf-8 -*-
"""Validações de fronteira para dados recebidos pela API.

As funções deste módulo não acessam banco nem ambiente. Elas convertem somente
entradas já consideradas válidas e levantam :class:`EntradaInvalida` nos demais
casos, para que a camada HTTP devolva um erro previsível em vez de um 500.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional
from uuid import UUID


MAX_CORPO_CONSULTA = 2_048
MAX_CORPO_ADMIN = 16_384
MAX_AUDITORES = 10_000
MAX_INSTALACOES_ATIVAS = 100
MAX_DIAS_OFFLINE = 30

_IBGE = re.compile(r"^\d{7}$")
_VERSAO = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-z.-]{1,32})?$")
_NONCE = re.compile(r"^[0-9A-Za-z._~-]{1,128}$")
_OPERADOR_SEM_CONTROLE = re.compile(r"^[^\x00-\x1f\x7f]{3,80}$")


class EntradaInvalida(ValueError):
    """Entrada externa não atende ao contrato da API."""


def validar_campos(
    corpo: dict[str, Any],
    permitidos: Iterable[str],
    *,
    obrigatorios: Iterable[str] = (),
    publico: bool = False,
) -> None:
    permitidos_set = frozenset(permitidos)
    extras = set(corpo) - permitidos_set
    if extras:
        if publico:
            raise EntradaInvalida("a requisição contém campos não permitidos")
        raise EntradaInvalida(f"campos não permitidos: {sorted(extras)}")
    faltando = [campo for campo in obrigatorios if campo not in corpo]
    if faltando:
        raise EntradaInvalida(f"campos obrigatórios ausentes: {sorted(faltando)}")


def uuid_canonico(valor: Any, campo: str = "instalacao_id") -> str:
    if not isinstance(valor, str):
        raise EntradaInvalida(f"{campo} deve ser UUID em texto")
    texto = valor.strip()
    if texto != valor:
        raise EntradaInvalida(f"{campo} não pode conter espaços nas extremidades")
    try:
        normalizado = str(UUID(texto))
    except (ValueError, AttributeError) as exc:
        raise EntradaInvalida(f"{campo} deve ser um UUID válido") from exc
    if texto != normalizado:
        raise EntradaInvalida(f"{campo} deve usar o formato UUID canônico")
    return normalizado


def codigo_ibge(valor: Any) -> str:
    if not isinstance(valor, str) or not _IBGE.fullmatch(valor):
        raise EntradaInvalida("codigo_ibge deve conter exatamente sete dígitos")
    return valor


def versao(valor: Any, campo: str = "versao_app") -> str:
    if not isinstance(valor, str) or not _VERSAO.fullmatch(valor):
        raise EntradaInvalida(f"{campo} deve usar formato de versão como 1.2.3")
    return valor


def nonce(valor: Any) -> str:
    if not isinstance(valor, str) or not _NONCE.fullmatch(valor):
        raise EntradaInvalida(
            "nonce deve ter de 1 a 128 caracteres alfanuméricos ou . _ ~ -"
        )
    return valor


def inteiro(
    valor: Any,
    campo: str,
    *,
    minimo: int = 1,
    maximo: int,
    opcional: bool = False,
) -> Optional[int]:
    if valor is None and opcional:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise EntradaInvalida(f"{campo} deve ser um número inteiro")
    if not minimo <= valor <= maximo:
        raise EntradaInvalida(f"{campo} deve estar entre {minimo} e {maximo}")
    return valor


def operador(valor: Any, *, obrigatorio: bool) -> str:
    if valor in (None, "") and not obrigatorio:
        return ""
    if not isinstance(valor, str) or not _OPERADOR_SEM_CONTROLE.fullmatch(valor.strip()):
        raise EntradaInvalida("X-Admin-Operador deve ter de 3 a 80 caracteres seguros")
    return valor.strip()


def requisicao_publica(corpo: dict[str, Any]) -> dict[str, str]:
    campos = frozenset({"instalacao_id", "codigo_ibge", "versao_app", "nonce"})
    validar_campos(corpo, campos, obrigatorios=campos, publico=True)
    return {
        "instalacao_id": uuid_canonico(corpo["instalacao_id"]),
        "codigo_ibge": codigo_ibge(corpo["codigo_ibge"]),
        "versao_app": versao(corpo["versao_app"]),
        "nonce": nonce(corpo["nonce"]),
    }
