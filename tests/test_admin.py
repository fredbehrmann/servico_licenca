# -*- coding: utf-8 -*-
"""Administração da allowlist: token, autorizar, revogar (instalação e município)."""

from __future__ import annotations

from tests.conftest import ADMIN

_REQ = {"instalacao_id": "inst-1", "codigo_ibge": "2927408",
        "versao_app": "1.0.0", "nonce": "n"}
_H = {"Authorization": f"Bearer {ADMIN}"}


def test_admin_sem_token_401(cliente):
    c, _, _ = cliente
    r = c.post("/admin/autorizar", json={"instalacao_id": "i", "codigo_ibge": "c"})
    assert r.status_code == 401


def test_admin_token_errado_401(cliente):
    c, _, _ = cliente
    r = c.post("/admin/autorizar", headers={"Authorization": "Bearer errado"},
               json={"instalacao_id": "i", "codigo_ibge": "c"})
    assert r.status_code == 401


def test_autorizar_e_listar(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": "inst-1", "codigo_ibge": "2927408", "max_usuarios": 3})
    lst = c.get("/admin/instalacoes", headers=_H).json()["instalacoes"]
    assert any(i["instalacao_id"] == "inst-1" and i["max_usuarios"] == 3 for i in lst)


def test_revogar_instalacao_reflete_na_consulta(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": "inst-1", "codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "ativa"
    c.post("/admin/revogar", headers=_H, json={"instalacao_id": "inst-1"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"


def test_revogar_municipio_reflete_na_consulta(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": "inst-1", "codigo_ibge": "2927408"})
    c.post("/admin/revogar-municipio", headers=_H, json={"codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"


def test_reativar_municipio_volta_a_ativa(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": "inst-1", "codigo_ibge": "2927408"})
    c.post("/admin/revogar-municipio", headers=_H, json={"codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"
    c.post("/admin/reativar-municipio", headers=_H, json={"codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "ativa"


def test_reautorizar_reativa_instalacao(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H, json={"instalacao_id": "inst-1", "codigo_ibge": "2927408"})
    c.post("/admin/revogar", headers=_H, json={"instalacao_id": "inst-1"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"
    # Autorizar de novo zera a revogação.
    c.post("/admin/autorizar", headers=_H, json={"instalacao_id": "inst-1", "codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "ativa"


def test_municipios_revogados_listagem(cliente):
    c, _, _ = cliente
    c.post("/admin/revogar-municipio", headers=_H, json={"codigo_ibge": "2927408"})
    lst = c.get("/admin/municipios-revogados", headers=_H).json()["codigos"]
    assert "2927408" in lst


def test_pagina_admin_publica_e_sem_dados(cliente):
    c, _, _ = cliente
    r = c.get("/admin")                      # a página em si não exige token
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "ADMIN_TOKEN" in r.text
    # nenhum dado de instalação embutido na página
    assert "instalacao_id" in r.text and "inst-1" not in r.text
