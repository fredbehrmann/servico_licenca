# -*- coding: utf-8 -*-
"""Barreiras de regressão para segredos, dados pessoais e painel administrativo."""

from __future__ import annotations

import logging

from app.painel import PAGINA_ADMIN
from tests.conftest import ADMIN, INSTALACAO_1


_H = {"Authorization": f"Bearer {ADMIN}"}
_REQ = {
    "instalacao_id": INSTALACAO_1,
    "codigo_ibge": "2927408",
    "versao_app": "1.0.0",
    "nonce": "nonce-privacidade",
}


def test_logs_nao_expoem_chave_token_assinatura_ou_dados_pessoais(
    cliente, par_de_teste, caplog,
):
    c, _, _ = cliente
    pem, _ = par_de_teste
    dados_proibidos = {
        ADMIN,
        pem.decode("utf-8").strip(),
        "123.456.789-00",
        "auditor@prefeitura.gov.br",
        "12.345.678/0001-90",
        "senha-supersecreta",
    }

    with caplog.at_level(logging.DEBUG):
        valida = c.post("/v1/consulta", json=_REQ)
        invalida = c.post("/v1/consulta", json={
            **_REQ,
            "cpf": "123.456.789-00",
            "email": "auditor@prefeitura.gov.br",
            "cnpj": "12.345.678/0001-90",
            "senha": "senha-supersecreta",
        })
        c.get(
            "/admin/instalacoes",
            headers={"Authorization": f"Bearer {ADMIN}-incorreto"},
        )

    assert valida.status_code == 200
    assert invalida.status_code == 400
    dados_proibidos.add(valida.json()["assinatura"])
    for segredo in dados_proibidos:
        assert segredo not in caplog.text


def test_token_incorreto_nao_permite_leitura_nem_alteracao(cliente):
    c, _, main = cliente
    errado = {"Authorization": "Bearer token-incorreto"}

    leitura = c.get("/admin/instalacoes", headers=errado)
    escrita = c.post("/admin/autorizar", headers=errado, json={
        "instalacao_id": INSTALACAO_1,
        "codigo_ibge": "2927408",
        "max_usuarios": 5,
    })

    assert leitura.status_code == 401
    assert escrita.status_code == 401
    assert main.estado.repo.obter_instalacao(INSTALACAO_1) is None


def test_painel_mantem_token_somente_na_sessao_da_aba():
    texto = PAGINA_ADMIN

    assert "sessionStorage.setItem('admtok'" in texto
    assert "sessionStorage.removeItem('admtok'" in texto
    assert "localStorage" not in texto
    assert "document.cookie" not in texto
    assert 'id="token" type="password"' in texto
    assert 'autocomplete="off"' in texto
