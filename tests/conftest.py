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
INSTALACAO_1 = "11111111-1111-4111-8111-111111111111"
INSTALACAO_2 = "22222222-2222-4222-8222-222222222222"
INSTALACAO_3 = "33333333-3333-4333-8333-333333333333"


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
    monkeypatch.setenv("LICENCA_PUBLICA_B64_ESPERADA", base64.b64encode(pub_raw).decode("ascii"))
    monkeypatch.setenv("LICENCA_AMBIENTE", "homologacao")
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN)
    recuperacao_privada = Ed25519PrivateKey.generate()
    recuperacao_pem = recuperacao_privada.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    recuperacao_publica = recuperacao_privada.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv("RECUPERACAO_HABILITADA", "true")
    monkeypatch.setenv("RECUPERACAO_PRIVADA_PEM", recuperacao_pem.decode("utf-8"))
    monkeypatch.setenv("RECUPERACAO_KEY_ID", "recuperacao-teste-v2")
    monkeypatch.setenv(
        "RECUPERACAO_PUBLICA_B64_ESPERADA",
        base64.b64encode(recuperacao_publica).decode("ascii"),
    )
    monkeypatch.setenv("RECUPERACAO_DUPLA_APROVACAO", "true")
    monkeypatch.setenv("OIDC_HABILITADO", "true")
    monkeypatch.setenv("OIDC_ISSUER", "https://login.exemplo.test/tenant/v2.0")
    monkeypatch.setenv("OIDC_AUTHORIZATION_ENDPOINT", "https://login.exemplo.test/authorize")
    monkeypatch.setenv("OIDC_TOKEN_ENDPOINT", "https://login.exemplo.test/token")
    monkeypatch.setenv("OIDC_JWKS_URI", "https://login.exemplo.test/jwks")
    monkeypatch.setenv("OIDC_CLIENT_ID", "cliente-teste")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "segredo-cliente-teste")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://servico.test/admin/callback")
    monkeypatch.setenv("OIDC_SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("OIDC_GRUPO_LICENCAS_OPERADOR", "grupo-licencas")
    monkeypatch.setenv("OIDC_GRUPO_RECUPERACAO_OPERADOR", "grupo-rec-operador")
    monkeypatch.setenv("OIDC_GRUPO_RECUPERACAO_APROVADOR", "grupo-rec-aprovador")
    monkeypatch.setenv("OIDC_GRUPO_AUDITORIA_LEITURA", "grupo-auditoria")
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
