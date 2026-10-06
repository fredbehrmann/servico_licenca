# -*- coding: utf-8 -*-
"""Contrato binário/textual compartilhado com o SICOF para recuperação.

O formato é deliberadamente estrito: JSON canônico, base64url sem padding e
limite pequeno. Alterações aqui exigem teste de paridade com o cliente real.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

LIMITE_TEXTO = 16 * 1024


class FormatoRecuperacaoInvalido(ValueError):
    """O texto não pertence ao contrato ou possui representação ambígua."""


def canonico(dados: dict[str, Any]) -> bytes:
    if not isinstance(dados, dict):
        raise FormatoRecuperacaoInvalido("O conteúdo deve ser um objeto JSON.")
    return json.dumps(
        dados, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _b64url_codificar(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _b64url_decodificar(texto: str) -> bytes:
    alfabeto = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if not texto or any(c not in alfabeto for c in texto):
        raise FormatoRecuperacaoInvalido("Codificação base64url inválida.")
    try:
        bruto = base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))
    except (ValueError, binascii.Error) as exc:
        raise FormatoRecuperacaoInvalido("Codificação base64url inválida.") from exc
    if _b64url_codificar(bruto) != texto:
        raise FormatoRecuperacaoInvalido("Codificação base64url não canônica.")
    return bruto


def _sem_duplicatas(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    saida: dict[str, Any] = {}
    for chave, valor in pares:
        if chave in saida:
            raise FormatoRecuperacaoInvalido("O JSON contém campos duplicados.")
        saida[chave] = valor
    return saida


def _json_estrito(bruto: bytes) -> dict[str, Any]:
    try:
        texto = bruto.decode("utf-8")
        dados = json.loads(texto, object_pairs_hook=_sem_duplicatas)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormatoRecuperacaoInvalido("JSON inválido.") from exc
    if not isinstance(dados, dict):
        raise FormatoRecuperacaoInvalido("O conteúdo deve ser um objeto JSON.")
    if canonico(dados) != bruto:
        raise FormatoRecuperacaoInvalido("O JSON não usa a representação canônica.")
    return dados


def codificar_solicitacao(payload: dict[str, Any]) -> str:
    return "TFRQ1." + _b64url_codificar(canonico(payload))


def decodificar_solicitacao(texto: str) -> dict[str, Any]:
    valor = (texto or "").strip()
    if len(valor.encode("utf-8")) > LIMITE_TEXTO or not valor.startswith("TFRQ1."):
        raise FormatoRecuperacaoInvalido("Solicitação inválida.")
    partes = valor.split(".")
    if len(partes) != 2:
        raise FormatoRecuperacaoInvalido("Solicitação inválida.")
    return _json_estrito(_b64url_decodificar(partes[1]))


def payload_token_para_assinar(payload: dict[str, Any]) -> bytes:
    return canonico(payload)


def codificar_token(payload: dict[str, Any], assinatura: bytes) -> str:
    return "TFR1." + _b64url_codificar(canonico(payload)) + "." + _b64url_codificar(assinatura)


def decodificar_token(texto: str) -> tuple[dict[str, Any], bytes]:
    valor = (texto or "").strip()
    if len(valor.encode("utf-8")) > LIMITE_TEXTO or not valor.startswith("TFR1."):
        raise FormatoRecuperacaoInvalido("Token inválido.")
    partes = valor.split(".")
    if len(partes) != 3:
        raise FormatoRecuperacaoInvalido("Token inválido.")
    payload = _json_estrito(_b64url_decodificar(partes[1]))
    assinatura = _b64url_decodificar(partes[2])
    if len(assinatura) != 64:
        raise FormatoRecuperacaoInvalido("Assinatura inválida.")
    return payload, assinatura

