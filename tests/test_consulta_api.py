# -*- coding: utf-8 -*-
"""POST /v1/consulta: assinatura verificável, nonce, vínculo, 400/429/503."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ADMIN, KEY_ID, verifica_assinatura

_REQ = {"instalacao_id": "inst-1", "codigo_ibge": "2927408",
        "versao_app": "1.0.0", "nonce": "nonce-xyz"}


def _autoriza(cliente):
    c, _, _ = cliente
    r = c.post("/admin/autorizar",
               headers={"Authorization": f"Bearer {ADMIN}"},
               json={"instalacao_id": "inst-1", "codigo_ibge": "2927408", "max_usuarios": 5})
    assert r.status_code == 200


def test_ativa_assinada_e_verificavel(cliente):
    c, pub, _ = cliente
    _autoriza(cliente)
    r = c.post("/v1/consulta", json=_REQ)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "ativa"
    assert corpo["nonce"] == "nonce-xyz"        # nonce volta igual
    assert corpo["key_id"] == KEY_ID
    verifica_assinatura(corpo, pub)             # assinatura confere


def test_nao_autorizada_tambem_vem_assinada(cliente):
    c, pub, _ = cliente
    r = c.post("/v1/consulta", json=_REQ)       # sem autorizar
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "instalacao_nao_autorizada"
    verifica_assinatura(corpo, pub)             # o "não" é assinado e confiável


def test_campo_ausente_da_400(cliente):
    c, _, _ = cliente
    r = c.post("/v1/consulta", json={"instalacao_id": "i", "codigo_ibge": "c", "nonce": "n"})
    assert r.status_code == 400


def test_rate_limit_429(monkeypatch, par_de_teste):
    pem, _ = par_de_teste
    monkeypatch.setenv("LICENCA_PRIVADA_PEM", pem.decode("utf-8"))
    monkeypatch.setenv("LICENCA_KEY_ID", KEY_ID)
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("LICENCA_RATE_MAX", "2")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app import main
    with TestClient(main.app) as c:
        assert c.post("/v1/consulta", json=_REQ).status_code == 200
        assert c.post("/v1/consulta", json=_REQ).status_code == 200
        assert c.post("/v1/consulta", json=_REQ).status_code == 429


def test_sem_chave_de_assinatura_da_503(monkeypatch):
    monkeypatch.delenv("LICENCA_PRIVADA_PEM", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app import main
    with TestClient(main.app) as c:
        r = c.post("/v1/consulta", json=_REQ)
        assert r.status_code == 503


def test_health_e_ready(cliente):
    c, _, _ = cliente
    assert c.get("/health").json()["ok"] is True
    assert c.get("/ready").status_code == 200
