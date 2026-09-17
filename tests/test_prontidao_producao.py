# -*- coding: utf-8 -*-
"""Etapa 8: produção falha fechada e saúde não revela configuração."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.assinador import Assinador
from app.configuracao import ConfiguracaoOperacional, diagnosticar
from app.repositorio import RepositorioMemoria
from tests.conftest import ADMIN, INSTALACAO_1, KEY_ID


def _config(**mudancas) -> ConfiguracaoOperacional:
    dados = {
        "ambiente": "producao", "ambiente_explicito": True,
        "database_url": "", "key_id": KEY_ID, "publica_esperada_b64": "",
        "admin_token": "token-forte-com-mais-de-32-caracteres-123456",
        "versao_minima": "1.0.0", "dias_offline": 7, "dias_validade": 365,
        "rate_max": 12, "rate_janela_s": 3600,
        "replicas": 1, "replicas_explicitas": True,
        "retencao_tentativas_dias": 90, "retencao_auditoria_dias": 730,
    }
    dados.update(mudancas)
    return ConfiguracaoOperacional(**dados)


def test_producao_sem_banco_nao_fica_pronta(par_de_teste):
    pem, _ = par_de_teste
    assinador = Assinador(pem, KEY_ID)
    cfg = _config(publica_esperada_b64=assinador.chave_publica_b64())
    diagnostico = diagnosticar(cfg, RepositorioMemoria(), assinador)
    assert diagnostico.pronto is False
    assert "database_url_postgres_obrigatoria" in diagnostico.problemas
    assert "repositorio_postgres_obrigatorio" in diagnostico.problemas


def test_ambiente_sem_chave_nao_fica_pronto():
    cfg = _config(ambiente="homologacao", database_url="")
    diagnostico = diagnosticar(cfg, RepositorioMemoria(), None)
    assert diagnostico.pronto is False
    assert "chave_privada_invalida_ou_ausente" in diagnostico.problemas


def test_producao_exige_token_forte_publica_esperada_e_uma_replica(par_de_teste):
    pem, _ = par_de_teste
    assinador = Assinador(pem, KEY_ID)
    cfg = _config(admin_token="fraco", replicas=2, publica_esperada_b64="")
    problemas = diagnosticar(cfg, RepositorioMemoria(), assinador).problemas
    assert "credencial_admin_fraca_ou_ausente" in problemas
    assert "chave_publica_esperada_ausente" in problemas
    assert "limitador_local_exige_uma_replica" in problemas


def test_producao_incompleta_mantem_health_mas_bloqueia_ready_e_consulta(
    monkeypatch, par_de_teste,
):
    pem, _ = par_de_teste
    monkeypatch.setenv("LICENCA_AMBIENTE", "producao")
    monkeypatch.setenv("LICENCA_KEY_ID", KEY_ID)
    monkeypatch.setenv("LICENCA_PRIVADA_PEM", pem.decode("utf-8"))
    monkeypatch.setenv("ADMIN_TOKEN", "token-forte-com-mais-de-32-caracteres-123456")
    monkeypatch.setenv("LICENCA_REPLICAS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app import main

    with TestClient(main.app) as cliente:
        assert cliente.get("/health").json() == {"ok": True}
        assert cliente.get("/ready").status_code == 503
        resposta = cliente.post("/v1/consulta", json={
            "instalacao_id": "inst-1", "codigo_ibge": "2927408",
            "versao_app": "1.0.0", "nonce": "n",
        })
        assert resposta.status_code == 503


def test_railway_usa_ready_como_healthcheck():
    raiz = Path(__file__).resolve().parents[1]
    assert '"healthcheckPath": "/ready"' in (raiz / "railway.json").read_text()


def test_auditoria_admin_registra_operador_alvo_e_resultado(cliente):
    c, _, main = cliente
    cabecalhos = {
        "Authorization": f"Bearer {ADMIN}",
        "X-Admin-Operador": "fred",
    }
    resposta = c.post("/admin/autorizar", headers=cabecalhos, json={
        "instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408",
    })
    assert resposta.status_code == 200
    eventos = main.estado.repo.listar_auditoria_admin()
    assert any(
        e["operador"] == "fred"
        and e["alvo"] == "/admin/autorizar"
        and e["resultado"] == "200"
        for e in eventos
    )


def test_retencao_remove_registros_antigos_e_preserva_recentes():
    repo = RepositorioMemoria()
    agora = datetime.now(timezone.utc)
    antigo = agora - timedelta(days=800)
    repo.registrar_tentativa("i-antiga", "2927408", "ativa", antigo)
    repo.registrar_tentativa("i-recente", "2927408", "ativa", agora)
    repo.registrar_auditoria_admin("fred", "POST", "/admin/x", "200", "", antigo)
    repo.registrar_auditoria_admin("fred", "POST", "/admin/y", "200", "", agora)

    removidos = repo.aplicar_retencao(agora, 90, 730)

    assert removidos["tentativas"] == 1
    assert removidos["auditoria_admin"] == 1
    assert repo.listar_tentativas()[0]["instalacao_id"] == "i-recente"
    assert repo.listar_auditoria_admin()[0]["alvo"] == "/admin/y"
