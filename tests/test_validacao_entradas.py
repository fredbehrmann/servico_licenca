# -*- coding: utf-8 -*-
"""Etapa 9: validação estrita das fronteiras públicas e administrativas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import ADMIN, INSTALACAO_1


_H = {"Authorization": f"Bearer {ADMIN}"}
_REQ = {
    "instalacao_id": INSTALACAO_1,
    "codigo_ibge": "2927408",
    "versao_app": "1.0.0",
    "nonce": "nonce-seguro_123",
}


def _corpo_licenca(**mudancas):
    agora = datetime.now(timezone.utc)
    corpo = {
        "codigo_ibge": "2927408",
        "status": "ativa",
        "inicio_em": (agora - timedelta(days=1)).isoformat(),
        "expira_em": (agora + timedelta(days=365)).isoformat(),
        "max_auditores": 5,
        "max_instalacoes_ativas": 1,
        "dias_offline": 7,
        "versao_minima": "1.0.0",
    }
    corpo.update(mudancas)
    return corpo


@pytest.mark.parametrize("instalacao_id", ["nao-e-uuid", "{11111111-1111-4111-8111-111111111111}", 123])
def test_consulta_recusa_uuid_invalido(cliente, instalacao_id):
    c, _, _ = cliente
    resposta = c.post("/v1/consulta", json={**_REQ, "instalacao_id": instalacao_id})
    assert resposta.status_code == 400
    assert "UUID" in resposta.json()["detail"]


@pytest.mark.parametrize("ibge", ["292740", "29274080", "29A7408", 2927408])
def test_consulta_recusa_ibge_que_nao_seja_texto_com_sete_digitos(cliente, ibge):
    c, _, _ = cliente
    resposta = c.post("/v1/consulta", json={**_REQ, "codigo_ibge": ibge})
    assert resposta.status_code == 400
    assert "sete dígitos" in resposta.json()["detail"]


def test_consulta_recusa_campo_extra_sem_refletir_o_nome(cliente):
    c, _, _ = cliente
    resposta = c.post("/v1/consulta", json={**_REQ, "cpf": "00000000000"})
    assert resposta.status_code == 400
    assert resposta.json()["detail"] == "a requisição contém campos não permitidos"
    assert "cpf" not in resposta.text.lower()


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("versao_app", "versao livre"), ("versao_app", "1.2"),
     ("nonce", ""), ("nonce", "tem espaço"), ("nonce", "x" * 129)],
)
def test_consulta_limita_formato_e_tamanho_dos_campos(cliente, campo, valor):
    c, _, _ = cliente
    assert c.post("/v1/consulta", json={**_REQ, campo: valor}).status_code == 400


def test_consulta_limita_tamanho_do_corpo(cliente):
    c, _, _ = cliente
    corpo = '{"campo":"' + ("x" * 3000) + '"}'
    resposta = c.post(
        "/v1/consulta", content=corpo,
        headers={"Content-Type": "application/json"},
    )
    assert resposta.status_code == 400
    assert "tamanho" in resposta.json()["detail"]


@pytest.mark.parametrize(
    "conteudo",
    ["[]", '"texto"', "{json-invalido", '{"valor": NaN}'],
)
def test_consulta_recusa_json_que_nao_seja_objeto_estrito(cliente, conteudo):
    c, _, _ = cliente
    resposta = c.post(
        "/v1/consulta", content=conteudo,
        headers={"Content-Type": "application/json"},
    )
    assert resposta.status_code == 400
    assert "traceback" not in resposta.text.lower()


def test_consulta_exige_content_type_json(cliente):
    c, _, _ = cliente
    resposta = c.post("/v1/consulta", content="{}", headers={"Content-Type": "text/plain"})
    assert resposta.status_code == 400


@pytest.mark.parametrize("valor", [-1, 0, "5", "cinco", True, 10001])
def test_admin_recusa_limite_invalido_sem_erro_500(cliente, valor):
    c, _, _ = cliente
    resposta = c.post("/admin/autorizar", headers=_H, json={
        "instalacao_id": INSTALACAO_1,
        "codigo_ibge": "2927408",
        "max_usuarios": valor,
    })
    assert resposta.status_code == 400
    assert "traceback" not in resposta.text.lower()


def test_admin_recusa_uuid_ibge_e_campo_extra(cliente):
    c, _, _ = cliente
    assert c.post("/admin/revogar", headers=_H, json={
        "instalacao_id": "invalida",
    }).status_code == 400
    assert c.post("/admin/revogar-municipio", headers=_H, json={
        "codigo_ibge": "123",
    }).status_code == 400
    resposta = c.post("/admin/reativar-municipio", headers=_H, json={
        "codigo_ibge": "2927408", "ignorar": True,
    })
    assert resposta.status_code == 400
    assert "ignorar" in resposta.json()["detail"]


@pytest.mark.parametrize(
    "mudancas",
    [
        {"inicio_em": "2026-01-01T00:00:00"},
        {"expira_em": "2025-01-01T00:00:00+00:00"},
        {"status": "estado-inexistente"},
        {"max_auditores": 0},
        {"max_auditores": "5"},
        {"max_instalacoes_ativas": 101},
        {"dias_offline": 31},
        {"versao_minima": "versão livre"},
        {"campo_desconhecido": "valor"},
    ],
)
def test_criacao_de_licenca_recusa_dados_incoerentes(cliente, mudancas):
    c, _, _ = cliente
    resposta = c.post("/admin/licencas", headers=_H, json=_corpo_licenca(**mudancas))
    assert resposta.status_code == 400
    assert "traceback" not in resposta.text.lower()


def test_reducao_de_vigencia_exige_confirmacao_explicita(cliente):
    c, _, _ = cliente
    criada = c.post("/admin/licencas", headers=_H, json=_corpo_licenca()).json()["licenca"]
    nova_data = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    url = f"/admin/licencas/{criada['licenca_id']}"

    sem_confirmar = c.patch(url, headers=_H, json={"expira_em": nova_data})
    assert sem_confirmar.status_code == 400
    assert "confirmar_encurtamento" in sem_confirmar.json()["detail"]

    confirmada = c.patch(url, headers=_H, json={
        "expira_em": nova_data,
        "confirmar_encurtamento_vigencia": True,
    })
    assert confirmada.status_code == 200


def test_erro_interno_nao_devolve_segredo_sql_ou_traceback(cliente):
    c, _, main = cliente

    def falhar():
        raise RuntimeError("postgresql://usuario:senha-supersecreta@banco SELECT segredo")

    main.estado.repo.listar_instalacoes = falhar
    resposta = c.get("/admin/instalacoes", headers=_H)
    assert resposta.status_code == 500
    assert resposta.json() == {"detail": "erro interno"}
    texto = resposta.text.lower()
    for proibido in ("senha-supersecreta", "select", "traceback", "postgresql"):
        assert proibido not in texto
