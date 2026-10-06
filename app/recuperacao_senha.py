# -*- coding: utf-8 -*-
"""Regras puras para validar solicitações e emitir tokens de recuperação."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

from app import recuperacao_contrato as contrato
from app.assinador_recuperacao import AssinadorRecuperacao

FINALIDADE_SOLICITACAO = "recuperacao_admin_solicitacao"
FINALIDADE_TOKEN = "recuperacao_admin"
EMISSOR = "techfisco-suporte"
TTL_SOLICITACAO = 2 * 60 * 60
TTL_TOKEN_MAX = 15 * 60
TOLERANCIA_RELOGIO = 5 * 60
METODOS_VERIFICACAO = frozenset({
    "contato_oficial_cadastrado",
    "videochamada_documentada",
    "presencial",
    "outro_escalonado",
})


class RecuperacaoInvalida(ValueError):
    """Solicitação ou transição de recuperação recusada."""


def digest_texto(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def validar_solicitacao(texto: str, *, agora: int | None = None) -> dict[str, Any]:
    instante = int(time.time()) if agora is None else int(agora)
    try:
        pedido = contrato.decodificar_solicitacao(texto)
        iat = int(pedido.get("iat"))
        exp = int(pedido.get("exp"))
    except (contrato.FormatoRecuperacaoInvalido, TypeError, ValueError) as exc:
        raise RecuperacaoInvalida("Solicitação inválida ou expirada.") from exc
    campos = {
        "schema", "finalidade", "request_id", "desafio", "installation_id",
        "usuario", "versao_app", "iat", "exp",
    }
    valido = (
        set(pedido) == campos
        and pedido.get("schema") == 1
        and pedido.get("finalidade") == FINALIDADE_SOLICITACAO
        and all(
            isinstance(pedido.get(nome), str) and 0 < len(pedido[nome]) <= limite
            for nome, limite in {
                "request_id": 256,
                "desafio": 256,
                "installation_id": 128,
                "usuario": 256,
                "versao_app": 64,
            }.items()
        )
        and iat <= instante + TOLERANCIA_RELOGIO
        and exp >= instante
        and exp > iat
        and exp - iat <= TTL_SOLICITACAO + TOLERANCIA_RELOGIO
    )
    if not valido:
        raise RecuperacaoInvalida("Solicitação inválida ou expirada.")
    return pedido


def mascarar_referencia(valor: str) -> str:
    texto = str(valor or "")
    if len(texto) <= 10:
        return texto[:2] + "***" if texto else "—"
    return texto[:6] + "…" + texto[-4:]


def resumo_solicitacao(
    pedido: dict[str, Any], instalacao: dict[str, Any] | None,
    licenca: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "request_id": pedido["request_id"],
        "installation_id": pedido["installation_id"],
        "usuario_mascarado": mascarar_referencia(pedido["usuario"]),
        "versao_app": pedido["versao_app"],
        "solicitado_em": int(pedido["iat"]),
        "expira_em": int(pedido["exp"]),
        "instalacao_conhecida": instalacao is not None,
        "status_instalacao": (instalacao or {}).get("status_instalacao"),
        "ultima_consulta_em": (instalacao or {}).get("ultima_consulta_em"),
        "licenca_id": (instalacao or {}).get("licenca_id"),
        "codigo_ibge": (instalacao or {}).get("codigo_ibge"),
        "nome_municipio": (licenca or {}).get("nome_municipio"),
        "status_licenca": (licenca or {}).get("status"),
    }


def registro_preparado(
    texto: str,
    pedido: dict[str, Any],
    *,
    operador: str,
    protocolo: str,
    justificativa: str,
    metodo_verificacao: str,
    canal_oficial_confirmado: bool,
    escalonamento_confirmado: bool,
    instalacao_conhecida: bool,
    agora: int | None = None,
) -> dict[str, Any]:
    instante = int(time.time()) if agora is None else int(agora)
    protocolo = (protocolo or "").strip()
    justificativa = (justificativa or "").strip()
    metodo = (metodo_verificacao or "").strip()
    if not 3 <= len(protocolo) <= 120:
        raise RecuperacaoInvalida("Informe um protocolo válido.")
    if not 20 <= len(justificativa) <= 1000:
        raise RecuperacaoInvalida("A justificativa deve ter entre 20 e 1000 caracteres.")
    if metodo not in METODOS_VERIFICACAO:
        raise RecuperacaoInvalida("Selecione um método de verificação válido.")
    if canal_oficial_confirmado is not True:
        raise RecuperacaoInvalida("Confirme o uso do canal oficial de atendimento.")
    if not instalacao_conhecida and (
        metodo != "outro_escalonado" or escalonamento_confirmado is not True
    ):
        raise RecuperacaoInvalida(
            "Instalação desconhecida exige escalonamento formal e documentado."
        )
    return {
        "request_id": pedido["request_id"],
        "request_digest": digest_texto(texto.strip()),
        "installation_id": pedido["installation_id"],
        "usuario_referencia": pedido["usuario"],
        "desafio_digest": digest_texto(pedido["desafio"]),
        "versao_app": pedido["versao_app"],
        "solicitado_em": int(pedido["iat"]),
        "solicitacao_expira_em": int(pedido["exp"]),
        "estado": "preparada",
        "operador_preparou": operador,
        "aprovador": None,
        "emitido_por": None,
        "protocolo": protocolo,
        "justificativa": justificativa,
        "metodo_verificacao": metodo,
        "canal_oficial_confirmado": True,
        "escalonamento_confirmado": bool(escalonamento_confirmado),
        "kid": None,
        "jti_digest": None,
        "token_digest": None,
        "emitido_em": None,
        "token_expira_em": None,
        "criado_em": instante,
        "atualizado_em": instante,
    }


def emitir_token(
    pedido: dict[str, Any],
    assinador: AssinadorRecuperacao,
    *,
    agora: int | None = None,
    ttl_segundos: int = TTL_TOKEN_MAX,
) -> tuple[str, dict[str, Any]]:
    instante = int(time.time()) if agora is None else int(agora)
    ttl = max(1, min(int(ttl_segundos), TTL_TOKEN_MAX))
    expira = min(int(pedido["exp"]), instante + ttl)
    if expira <= instante:
        raise RecuperacaoInvalida("Solicitação inválida ou expirada.")
    payload = {
        "schema": 1,
        "finalidade": FINALIDADE_TOKEN,
        "iss": EMISSOR,
        "kid": assinador.key_id,
        "installation_id": pedido["installation_id"],
        "request_id": pedido["request_id"],
        "desafio": pedido["desafio"],
        "usuario": pedido["usuario"],
        "iat": instante,
        "exp": expira,
        "jti": secrets.token_urlsafe(24),
    }
    assinatura = assinador.assinar(payload)
    return contrato.codificar_token(payload, assinatura), payload

