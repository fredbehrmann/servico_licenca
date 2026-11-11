# -*- coding: utf-8 -*-
"""Versão de avaliação municipal sem regressão para instalações existentes."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app import municipios
from tests.conftest import ADMIN, INSTALACAO_1, INSTALACAO_2, INSTALACAO_3


_H = {"Authorization": f"Bearer {ADMIN}"}
_IBGE = "2927408"


def _habilitar_trial(c, main, *, maximo: int = 1) -> None:
    main.estado.cfg = replace(main.estado.cfg, trial_ativo=True)
    resposta = c.post("/admin/municipios-trial", headers=_H, json={
        "codigo_ibge": _IBGE,
        "ativo": True,
        "max_instalacoes_trial": maximo,
    })
    assert resposta.status_code == 200


def _consulta(instalacao: str, versao: str = "1.0.12") -> dict:
    return {
        "instalacao_id": instalacao,
        "codigo_ibge": _IBGE,
        "versao_app": versao,
        "nonce": f"nonce-{instalacao[-4:]}-{versao}",
    }


def test_catalogo_autocomplete_nome_uf_e_codigo():
    por_nome = municipios.buscar("salv", 5)
    por_nome_completo = municipios.buscar("Salvador", 5)
    por_codigo = municipios.buscar("2927408", 5)
    assert por_nome[0] == {
        "codigo_ibge": "2927408", "nome": "Salvador", "uf": "BA",
        "rotulo": "Salvador/BA — 2927408",
    }
    assert por_nome_completo[0] == por_nome[0]
    assert por_codigo[0] == por_nome[0]
    assert municipios.buscar("s", 5) == []


def test_painel_preenche_ibge_apos_selecao_do_municipio(cliente):
    c, _, _ = cliente
    texto = c.get("/admin").text

    assert "buscarMunicipioTrial()" in texto
    assert "_sugestoesTrial=sugestoes" in texto
    assert "window[destino]" not in texto
    assert "if(exatos.length===1)item=exatos[0]" in texto
    assert 'id="t_ibge" readonly' in texto


def test_trial_nasce_na_primeira_consulta_compativel(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)

    resposta = c.post("/v1/consulta", json=_consulta(INSTALACAO_1)).json()

    assert resposta["status"] == "ativa"
    assert resposta["plano"] == "trial"
    assert resposta["max_usuarios"] is None
    licenca = main.estado.repo.obter_licenca(
        main.estado.repo.obter_instalacao(INSTALACAO_1)["licenca_id"]
    )
    assert licenca["plano"] == "trial"
    inicio = datetime.fromisoformat(licenca["inicio_em"])
    fim = datetime.fromisoformat(licenca["expira_em"])
    assert fim - inicio == timedelta(days=30)


def test_cliente_antigo_nao_recebe_plano_nem_ganha_trial(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)

    resposta = c.post("/v1/consulta", json=_consulta(INSTALACAO_1, "1.0.0")).json()

    assert resposta["status"] == "instalacao_nao_autorizada"
    assert "plano" not in resposta
    assert main.estado.repo.obter_instalacao(INSTALACAO_1) is None


def test_ibge_vazio_continua_erro_e_nao_cria_trial(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)
    requisicao = _consulta(INSTALACAO_1)
    requisicao["codigo_ibge"] = ""

    resposta = c.post("/v1/consulta", json=requisicao)

    assert resposta.status_code == 400
    assert main.estado.repo.obter_instalacao(INSTALACAO_1) is None
    assert main.estado.repo.listar_licencas() == []


def test_instalacao_conhecida_preserva_modelo_contratado(cliente):
    c, _, main = cliente
    main.estado.repo.autorizar(INSTALACAO_1, _IBGE, 4)
    _habilitar_trial(c, main)

    resposta = c.post("/v1/consulta", json=_consulta(INSTALACAO_1)).json()

    assert resposta["status"] == "ativa"
    assert resposta["plano"] == "contratado"
    assert resposta["max_usuarios"] == 4
    assert main.estado.repo.obter_instalacao(INSTALACAO_1)["licenca_id"] is None


def test_desabilitar_novas_emissoes_nao_revoga_trial_existente(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)
    primeira = c.post("/v1/consulta", json=_consulta(INSTALACAO_1)).json()
    assert primeira["status"] == "ativa"
    assert c.post("/admin/municipios-trial", headers=_H, json={
        "codigo_ibge": _IBGE, "ativo": False, "max_instalacoes_trial": 1,
    }).status_code == 200

    existente = c.post("/v1/consulta", json=_consulta(INSTALACAO_1)).json()
    nova = c.post("/v1/consulta", json=_consulta(INSTALACAO_2)).json()
    assert existente["status"] == "ativa" and existente["plano"] == "trial"
    assert nova["status"] == "instalacao_nao_autorizada"


def test_trial_pode_ser_revogado_mas_nao_renovado_como_contrato(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)
    c.post("/v1/consulta", json=_consulta(INSTALACAO_1))
    licenca_id = main.estado.repo.obter_instalacao(INSTALACAO_1)["licenca_id"]

    renovacao = c.patch(
        f"/admin/licencas/{licenca_id}", headers=_H,
        json={"expira_em": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat()},
    )
    revogacao = c.patch(
        f"/admin/licencas/{licenca_id}", headers=_H, json={"status": "revogada"},
    )

    assert renovacao.status_code == 400 and "conversão" in renovacao.json()["detail"]
    assert revogacao.status_code == 200
    assert c.post("/v1/consulta", json=_consulta(INSTALACAO_1)).json()["status"] == "revogada"


def test_conversao_preserva_licenca_e_instalacao(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)
    c.post("/v1/consulta", json=_consulta(INSTALACAO_3))
    instalacao_antes = main.estado.repo.obter_instalacao(INSTALACAO_3)
    licenca_id = instalacao_antes["licenca_id"]
    agora = datetime.now(timezone.utc)

    convertida = c.post(
        f"/admin/licencas/{licenca_id}/converter-contrato", headers=_H, json={
            "status": "ativa",
            "inicio_em": agora.isoformat(),
            "expira_em": (agora + timedelta(days=365)).isoformat(),
            "max_auditores": 8,
            "max_instalacoes_ativas": 1,
            "dias_offline": 7,
            "versao_minima": "1.0.0",
        },
    )

    assert convertida.status_code == 200
    assert convertida.json()["licenca"]["plano"] == "contratado"
    assert main.estado.repo.obter_instalacao(INSTALACAO_3)["licenca_id"] == licenca_id
    assert main.estado.repo.listar_municipios_trial()[0]["ativo"] is False
    resposta = c.post("/v1/consulta", json=_consulta(INSTALACAO_3)).json()
    assert resposta["plano"] == "contratado" and resposta["max_usuarios"] == 8


def test_trial_aceita_associacao_manual_de_instalacao_conhecida(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main, maximo=2)
    c.post("/v1/consulta", json=_consulta(INSTALACAO_1))
    licenca_id = main.estado.repo.obter_instalacao(INSTALACAO_1)["licenca_id"]
    main.estado.repo.autorizar(INSTALACAO_2, "3550308", 3)

    resposta = c.post(
        f"/admin/licencas/{licenca_id}/instalacoes", headers=_H,
        json={
            "instalacao_id": INSTALACAO_2,
            "status": "ativa",
            "confirmar_transferencia": True,
        },
    )

    assert resposta.status_code == 200
    assert main.estado.repo.obter_instalacao(INSTALACAO_2)["licenca_id"] == licenca_id


def test_painel_cria_trial_ao_associar_primeira_instalacao(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)

    assert main.estado.repo.listar_licencas() == []
    resposta = c.post(
        f"/admin/municipios-trial/{_IBGE}/instalacoes", headers=_H,
        json={"instalacao_id": INSTALACAO_1, "status": "ativa"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["licenca_criada"] is True
    assert corpo["licenca"]["plano"] == "trial"
    assert corpo["instalacao"]["licenca_id"] == corpo["licenca"]["licenca_id"]
    inicio = datetime.fromisoformat(corpo["licenca"]["inicio_em"])
    fim = datetime.fromisoformat(corpo["licenca"]["expira_em"])
    assert fim - inicio == timedelta(days=30)


def test_associacao_manual_trial_exige_confirmacao_para_instalacao_existente(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)
    main.estado.repo.autorizar(INSTALACAO_2, "3550308", 3)

    sem_confirmacao = c.post(
        f"/admin/municipios-trial/{_IBGE}/instalacoes", headers=_H,
        json={"instalacao_id": INSTALACAO_2, "status": "ativa"},
    )
    confirmada = c.post(
        f"/admin/municipios-trial/{_IBGE}/instalacoes", headers=_H,
        json={
            "instalacao_id": INSTALACAO_2,
            "status": "ativa",
            "confirmar_transferencia": True,
        },
    )

    assert sem_confirmacao.status_code == 409
    assert "continua válida" in sem_confirmacao.json()["detail"]
    assert confirmada.status_code == 200
    assert confirmada.json()["licenca_criada"] is True
    assert main.estado.repo.obter_instalacao(INSTALACAO_2)["licenca_id"]


def test_consulta_de_suporte_reune_plano_datas_e_municipio(cliente):
    c, _, main = cliente
    _habilitar_trial(c, main)
    c.post("/v1/consulta", json=_consulta(INSTALACAO_1))

    resposta = c.get(
        f"/admin/suporte/instalacoes?termo={INSTALACAO_1}", headers=_H,
    )

    assert resposta.status_code == 200
    registro = resposta.json()["instalacoes"][0]
    assert registro["plano"] == "trial"
    assert registro["nome_municipio"] == "Salvador"
    assert registro["ultima_versao_app"] == "1.0.12"
    assert registro["criada_em"] and registro["ultima_consulta_em"]
