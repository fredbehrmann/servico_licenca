# -*- coding: utf-8 -*-
"""Assinatura Ed25519 da resposta de licença.

A chave **privada** vem de um segredo de ambiente (``LICENCA_PRIVADA_PEM``) — na
Railway, uma variável secreta. Nunca de arquivo no repositório nem do instalador.
O serviço é o único lugar onde a privada existe.
"""

from __future__ import annotations

import base64
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from app.contrato import payload_para_assinar


class ChaveIndisponivel(RuntimeError):
    """A privada não pôde ser carregada. O serviço não deve assinar sem ela."""


class Assinador:
    def __init__(self, pem: bytes, key_id: str) -> None:
        try:
            priv = load_pem_private_key(pem, password=None)
        except Exception as exc:   # PEM inválido, cifrado, etc.
            raise ChaveIndisponivel(f"não foi possível carregar a privada: {exc}") from exc
        if not isinstance(priv, Ed25519PrivateKey):
            raise ChaveIndisponivel("a chave privada não é Ed25519")
        self._priv = priv
        self.key_id = key_id

    def assinar(self, resposta: dict[str, Any]) -> str:
        """Devolve a assinatura base64 do payload canônico (resposta sem assinatura)."""
        return base64.b64encode(self._priv.sign(payload_para_assinar(resposta))).decode("ascii")

    def chave_publica_b64(self) -> str:
        """Pública em base64 (RAW 32 bytes) — para conferir o par no deploy."""
        from cryptography.hazmat.primitives import serialization
        raw = self._priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        return base64.b64encode(raw).decode("ascii")


def assinador_do_ambiente(pem_texto: str, key_id: str) -> Assinador:
    if not (pem_texto or "").strip():
        raise ChaveIndisponivel(
            "LICENCA_PRIVADA_PEM ausente. Defina o segredo com o PEM da chave privada Ed25519."
        )
    return Assinador(pem_texto.encode("utf-8"), key_id)
