# -*- coding: utf-8 -*-
"""Assinador Ed25519 exclusivo do domínio de recuperação de senha."""

from __future__ import annotations

import base64
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.recuperacao_contrato import payload_token_para_assinar


class ChaveRecuperacaoIndisponivel(RuntimeError):
    """A chave privada de recuperação não pôde ser carregada."""


class AssinadorRecuperacao:
    def __init__(self, pem: bytes, key_id: str) -> None:
        try:
            privada = load_pem_private_key(pem, password=None)
        except Exception as exc:
            raise ChaveRecuperacaoIndisponivel(
                "não foi possível carregar a chave privada de recuperação"
            ) from exc
        if not isinstance(privada, Ed25519PrivateKey):
            raise ChaveRecuperacaoIndisponivel("a chave de recuperação não é Ed25519")
        if not (key_id or "").strip():
            raise ChaveRecuperacaoIndisponivel("o identificador da chave está ausente")
        self._privada = privada
        self.key_id = key_id.strip()

    def assinar(self, payload: dict[str, Any]) -> bytes:
        return self._privada.sign(payload_token_para_assinar(payload))

    def chave_publica_b64(self) -> str:
        bruto = self._privada.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(bruto).decode("ascii")


def assinador_recuperacao_do_ambiente(
    pem_texto: str, key_id: str,
) -> AssinadorRecuperacao:
    if not (pem_texto or "").strip():
        raise ChaveRecuperacaoIndisponivel("RECUPERACAO_PRIVADA_PEM ausente")
    return AssinadorRecuperacao(pem_texto.encode("utf-8"), key_id)

