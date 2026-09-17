# -*- coding: utf-8 -*-
"""Etapa 3 — contrato real, instalações vinculadas e administração."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import licencas
from app.repositorio import RepositorioMemoria
from app.servico import Config, decidir
from tests.conftest import ADMIN, INSTALACAO_1, INSTALACAO_2


_AGORA = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
_IBGE = "2927408"
_REQ = {
    "instalacao_id": INSTALACAO_1, "codigo_ibge": _IBGE,
    "versao_app": "1.0.0", "nonce": "n-contrato",
}
_H = {"Authorization": f"Bearer {ADMIN}"}


def _licenca(**mudancas):
    corpo = {
        "licenca_id": "lic-1", "codigo_ibge": _IBGE, "status": "ativa",
        "inicio_em": (_AGORA - timedelta(days=30)).isoformat(),
        "expira_em": (_AGORA + timedelta(days=30)).isoformat(),
        "max_auditores": 8, "max_instalacoes_ativas": 1,
        "dias_offline": 7, "versao_minima": "1.2.0",
    }
    corpo.update(mudancas)
    return licencas.normalizar_criacao(corpo, agora=_AGORA)


def _repo_contratual(**mudancas):
    repo = RepositorioMemoria()
    criada = repo.criar_licenca(_licenca(**mudancas))
    repo.associar_instalacao(_REQ["instalacao_id"], criada["licenca_id"], "ativa", _AGORA)
    return repo


def test_modelo_valida_datas_e_campos_imutaveis():
    with pytest.raises(licencas.LicencaInvalida, match="posterior"):
        _licenca(expira_em=(_AGORA - timedelta(days=31)).isoformat())
    existente = _licenca()
    with pytest.raises(licencas.LicencaInvalida, match="imutáveis"):
        licencas.normalizar_atualizacao(existente, {"codigo_ibge": "3550308"}, _AGORA)


def test_resposta_usa_vigencia_e_limites_do_contrato():
    resposta = decidir(_REQ, _repo_contratual(), Config(dias_offline=99), _AGORA)
    assert resposta["status"] == "ativa"
    assert resposta["max_usuarios"] == 8
    assert resposta["versao_minima"] == "1.2.0"
    assert resposta["expira_em"] == (_AGORA + timedelta(days=30)).isoformat()
    assert resposta["offline_ate"] == (_AGORA + timedelta(days=7)).isoformat()


def test_licenca_antes_do_inicio_ainda_nao_autoriza():
    inicio = _AGORA + timedelta(days=1)
    repo = _repo_contratual(
        inicio_em=inicio.isoformat(),
        expira_em=(inicio + timedelta(days=365)).isoformat(),
    )

    resposta = decidir(_REQ, repo, Config(), _AGORA)

    assert resposta["status"] == "instalacao_nao_autorizada"
    assert resposta["max_usuarios"] is None
    assert resposta["offline_ate"] is None


def test_offline_nunca_ultrapassa_fim_contratual():
    fim = _AGORA + timedelta(hours=12)
    resposta = decidir(_REQ, _repo_contratual(expira_em=fim.isoformat()), Config(), _AGORA)
    assert resposta["status"] == "ativa"
    assert resposta["offline_ate"] == fim.isoformat()
    assert resposta["expira_em"] == fim.isoformat()


def test_consultas_nao_renovam_a_data_final_do_contrato():
    fim = _AGORA + timedelta(days=30)
    repo = _repo_contratual(expira_em=fim.isoformat())

    primeira = decidir(_REQ, repo, Config(), _AGORA)
    segunda = decidir(
        {**_REQ, "nonce": "n-consulta-posterior"},
        repo,
        Config(),
        _AGORA + timedelta(days=5),
    )

    assert primeira["expira_em"] == fim.isoformat()
    assert segunda["expira_em"] == fim.isoformat()
    assert repo.obter_licenca("lic-1")["expira_em"] == fim.isoformat()


def test_contrato_vencido_retorna_expirada_sem_prazo_offline():
    repo = _repo_contratual(
        inicio_em=(_AGORA - timedelta(days=60)).isoformat(),
        expira_em=(_AGORA - timedelta(seconds=1)).isoformat(),
    )
    resposta = decidir(_REQ, repo, Config(), _AGORA)
    assert resposta["status"] == "expirada"
    assert resposta["offline_ate"] is None
    assert resposta["expira_em"] == (_AGORA - timedelta(seconds=1)).isoformat()


@pytest.mark.parametrize(
    ("status", "esperado"),
    [("pendente", "instalacao_nao_autorizada"), ("suspensa", "revogada"),
     ("revogada", "revogada"), ("expirada", "expirada")],
)
def test_estados_internos_sao_traduzidos_para_contrato_v1(status, esperado):
    resposta = decidir(_REQ, _repo_contratual(status=status), Config(), _AGORA)
    assert resposta["status"] == esperado


def test_contingencia_nao_autoriza_e_nao_consumiu_instalacao_ativa():
    repo = RepositorioMemoria()
    repo.criar_licenca(_licenca())
    repo.associar_instalacao("contingencia", "lic-1", "contingencia", _AGORA)
    assert repo.contar_instalacoes_ativas("lic-1") == 0
    resposta = decidir({**_REQ, "instalacao_id": "contingencia"}, repo, Config(), _AGORA)
    assert resposta["status"] == "instalacao_nao_autorizada"


def test_limite_de_instalacoes_ativas_e_vinculo_exclusivo():
    repo = _repo_contratual()
    with pytest.raises(ValueError, match="limite"):
        repo.associar_instalacao("inst-2", "lic-1", "ativa", _AGORA)
    repo.criar_licenca(_licenca(licenca_id="lic-2", codigo_ibge="3550308"))
    with pytest.raises(ValueError, match="outra licença"):
        repo.associar_instalacao(_REQ["instalacao_id"], "lic-2", "contingencia", _AGORA)


def test_nao_cria_contrato_paralelo_para_mesmo_municipio():
    repo = RepositorioMemoria()
    repo.criar_licenca(_licenca())
    with pytest.raises(ValueError, match="licença em aberto"):
        repo.criar_licenca(_licenca(licenca_id="lic-paralela"))


def test_nao_reduz_limite_abaixo_das_instalacoes_ativas():
    repo = _repo_contratual(max_instalacoes_ativas=2)
    repo.associar_instalacao("inst-2", "lic-1", "ativa", _AGORA)
    existente = repo.obter_licenca("lic-1")
    alterada = licencas.normalizar_atualizacao(
        existente, {"max_instalacoes_ativas": 1}, _AGORA,
    )
    with pytest.raises(ValueError, match="quantidade de instalações ativas"):
        repo.atualizar_licenca("lic-1", alterada)


def test_api_admin_cria_renova_associa_e_registra_historico(cliente):
    c, _, _ = cliente
    inicio = (_AGORA - timedelta(days=1)).isoformat()
    fim = (_AGORA + timedelta(days=30)).isoformat()
    criada = c.post("/admin/licencas", headers=_H, json={
        "codigo_ibge": _IBGE, "status": "ativa", "inicio_em": inicio,
        "expira_em": fim, "max_auditores": 6, "max_instalacoes_ativas": 1,
        "dias_offline": 4, "versao_minima": "1.0.0",
    })
    assert criada.status_code == 200
    contrato = criada.json()["licenca"]
    licenca_id = contrato["licenca_id"]

    associada = c.post(
        f"/admin/licencas/{licenca_id}/instalacoes", headers=_H,
        json={"instalacao_id": _REQ["instalacao_id"], "status": "ativa"},
    )
    assert associada.status_code == 200
    consulta = c.post("/v1/consulta", json=_REQ).json()
    assert consulta["status"] == "ativa" and consulta["max_usuarios"] == 6

    novo_fim = (_AGORA + timedelta(days=365)).isoformat()
    renovada = c.patch(
        f"/admin/licencas/{licenca_id}", headers=_H,
        json={"expira_em": novo_fim, "status": "ativa"},
    )
    assert renovada.status_code == 200
    assert renovada.json()["licenca"]["expira_em"] == novo_fim

    eventos = c.get(
        f"/admin/licencas/{licenca_id}/historico", headers=_H,
    ).json()["eventos"]
    assert {evento["acao"] for evento in eventos} == {
        "licenca_criada", "instalacao_associada", "licenca_atualizada",
    }


def test_api_impede_segunda_instalacao_ativa_mas_aceita_contingencia(cliente):
    c, _, _ = cliente
    criada = c.post("/admin/licencas", headers=_H, json={
        "codigo_ibge": _IBGE, "status": "ativa",
        "inicio_em": (_AGORA - timedelta(days=1)).isoformat(),
        "expira_em": (_AGORA + timedelta(days=30)).isoformat(),
        "max_auditores": 5,
    }).json()["licenca"]
    url = f"/admin/licencas/{criada['licenca_id']}/instalacoes"
    assert c.post(url, headers=_H, json={"instalacao_id": INSTALACAO_1, "status": "ativa"}).status_code == 200
    recusada = c.post(url, headers=_H, json={"instalacao_id": INSTALACAO_2, "status": "ativa"})
    assert recusada.status_code == 400 and "limite" in recusada.json()["detail"]
    assert c.post(url, headers=_H, json={"instalacao_id": INSTALACAO_2, "status": "contingencia"}).status_code == 200


def test_painel_expoe_operacoes_contratuais(cliente):
    c, _, _ = cliente
    texto = c.get("/admin").text
    for trecho in ("Nova licença contratual", "Associar instalação", "Renovar", "Histórico"):
        assert trecho in texto
