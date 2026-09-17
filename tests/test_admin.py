# -*- coding: utf-8 -*-
"""Administração da allowlist: token, autorizar, revogar (instalação e município)."""

from __future__ import annotations

from dataclasses import replace

from tests.conftest import ADMIN, INSTALACAO_1, INSTALACAO_2, INSTALACAO_3

_REQ = {"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408",
        "versao_app": "1.0.0", "nonce": "n"}
_H = {"Authorization": f"Bearer {ADMIN}"}


def test_tentativas_sem_token_401(cliente):
    c, _, _ = cliente
    assert c.get("/admin/tentativas").status_code == 401


def test_tentativas_aparecem_apos_consulta(cliente):
    c, _, _ = cliente
    req = {"instalacao_id": INSTALACAO_2, "codigo_ibge": "2927408", "versao_app": "1.0.0", "nonce": "n9"}
    assert c.post("/v1/consulta", json=req).status_code == 200
    lst = c.get("/admin/tentativas", headers=_H).json()["tentativas"]
    assert any(t["instalacao_id"] == INSTALACAO_2 for t in lst)


def test_pagina_admin_tem_logo_e_consultas(cliente):
    c, _, _ = cliente
    r = c.get("/admin")
    assert r.status_code == 200
    assert "Consultas recentes" in r.text
    assert "Administração de licenças" in r.text
    assert "data:image/png;base64," in r.text   # logo do TechFisco embutida
    assert "<th>IBGE</th>" in r.text            # vínculo municipal visível na listagem


def test_admin_sem_token_401(cliente):
    c, _, _ = cliente
    r = c.post("/admin/autorizar", json={"instalacao_id": "i", "codigo_ibge": "c"})
    assert r.status_code == 401


def test_admin_token_errado_401(cliente):
    c, _, _ = cliente
    r = c.post("/admin/autorizar", headers={"Authorization": "Bearer errado"},
               json={"instalacao_id": "i", "codigo_ibge": "c"})
    assert r.status_code == 401


def test_token_correto_sem_operador_em_producao_da_403(cliente):
    c, _, main = cliente
    main.estado.cfg = replace(main.estado.cfg, ambiente="producao")

    resposta = c.get("/admin/instalacoes", headers=_H)

    assert resposta.status_code == 403
    assert "operador" in resposta.json()["detail"]


def test_parametro_com_tipo_invalido_da_422(cliente):
    c, _, _ = cliente
    resposta = c.get("/admin/tentativas?limite=texto", headers=_H)
    assert resposta.status_code == 422


def test_autorizar_e_listar(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408", "max_usuarios": 3})
    lst = c.get("/admin/instalacoes", headers=_H).json()["instalacoes"]
    assert any(i["instalacao_id"] == INSTALACAO_1 and i["max_usuarios"] == 3 for i in lst)


def test_revogar_instalacao_reflete_na_consulta(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "ativa"
    c.post("/admin/revogar", headers=_H, json={"instalacao_id": INSTALACAO_1})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"


def test_revogar_municipio_reflete_na_consulta(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408"})
    c.post("/admin/revogar-municipio", headers=_H, json={"codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"


def test_reativar_municipio_volta_a_ativa(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408"})
    c.post("/admin/revogar-municipio", headers=_H, json={"codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"
    c.post("/admin/reativar-municipio", headers=_H, json={"codigo_ibge": "2927408"})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "ativa"


def test_reautorizar_reativa_instalacao(cliente):
    c, _, _ = cliente
    c.post("/admin/autorizar", headers=_H, json={"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408"})
    c.post("/admin/revogar", headers=_H, json={"instalacao_id": INSTALACAO_1})
    assert c.post("/v1/consulta", json=_REQ).json()["status"] == "revogada"
    # Autorizar de novo zera a revogação.
    c.post("/admin/autorizar", headers=_H, json={"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408"})
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


def test_autorizacao_legada_fecha_depois_da_migracao(cliente):
    c, _, main = cliente
    from datetime import datetime, timedelta, timezone
    from app.migracao_legado import aplicar_documento, gerar_documento

    c.post("/admin/autorizar", headers=_H,
           json={"instalacao_id": INSTALACAO_2, "codigo_ibge": "2927408", "max_usuarios": 3})
    documento = gerar_documento(main.estado.repo)
    decisao = documento["decisoes"][0]
    agora = datetime.now(timezone.utc)
    decisao.update({
        "confirmado": True, "status": "ativa",
        "inicio_em": (agora - timedelta(days=1)).isoformat(),
        "expira_em": (agora + timedelta(days=365)).isoformat(),
        "instalacao_ativa_id": INSTALACAO_2, "instalacoes_contingencia_ids": [],
    })
    aplicar_documento(documento, main.estado.repo, "backup-teste", agora=agora)

    status = c.get("/admin/migracao-legado/status", headers=_H).json()
    assert status == {"ok": True, "concluida": True}
    nova = c.post("/admin/autorizar", headers=_H,
                  json={"instalacao_id": INSTALACAO_3, "codigo_ibge": "2927408"})
    assert nova.status_code == 409
