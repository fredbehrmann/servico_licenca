# -*- coding: utf-8 -*-
"""Fixtures do serviço de licença: par Ed25519 de teste e cliente HTTP."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.contrato import payload_para_assinar

KEY_ID = "teste-servico"
ADMIN = "tok-admin-teste"


@pytest.fixture
def par_de_teste():
    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return pem, pub_raw


@pytest.fixture
def cliente(monkeypatch, par_de_teste):
    """TestClient com estado montado: chave de teste, admin token, repo em memória."""
    pem, pub_raw = par_de_teste
    monkeypatch.setenv("LICENCA_PRIVADA_PEM", pem.decode("utf-8"))
    monkeypatch.setenv("LICENCA_KEY_ID", KEY_ID)
    monkeypatch.setenv("LICENCA_AMBIENTE", "homologacao")
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as c:
        yield c, pub_raw, main


def verifica_assinatura(resposta: dict, pub_raw: bytes) -> None:
    """Levanta se a assinatura não conferir — usa a MESMA canonização do contrato."""
    assinatura = base64.b64decode(resposta["assinatura"])
    Ed25519PublicKey.from_public_bytes(pub_raw).verify(
        assinatura, payload_para_assinar(resposta)
    )
