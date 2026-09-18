# -*- coding: utf-8 -*-
"""Monitor do piloto agrega sinais sem copiar identificadores ou dados pessoais."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "monitorar_piloto.py"
SPEC = importlib.util.spec_from_file_location("monitorar_piloto", CAMINHO)
assert SPEC and SPEC.loader
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def test_url_exige_https_e_recusa_credencial():
    assert monitor.normalizar_url("https://licenca.example") == "https://licenca.example"
    with pytest.raises(ValueError):
        monitor.normalizar_url("http://licenca.example")
    with pytest.raises(ValueError):
        monitor.normalizar_url("https://usuario:senha@licenca.example")
    assert monitor.normalizar_url(
        "http://127.0.0.1:8765", permitir_http_local=True
    ) == "http://127.0.0.1:8765"


def test_resumo_admin_descarta_identificadores_e_dados_pessoais():
    agora = datetime(2026, 9, 17, tzinfo=timezone.utc)
    resumo = monitor.resumir_admin(
        [{
            "licenca_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "codigo_ibge": "2927408",
            "status": "ativa",
            "expira_em": (agora + timedelta(days=5)).isoformat(),
        }],
        [{
            "instalacao_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "codigo_ibge": "2927408",
            "status": "ativa",
        }],
        [{
            "operador": "nome.real@example.gov.br",
            "acao": "PATCH",
            "alvo": "/admin/licencas/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "resultado": "200",
        }],
        agora=agora,
        dias_alerta_expiracao=30,
    )
    serializado = json.dumps(resumo, sort_keys=True)
    assert resumo["licencas_total"] == 1
    assert resumo["licencas_expirando"] == 1
    assert resumo["acoes_admin_por_rota"] == {"PATCH /admin/licencas/{id}": 1}
    for segredo in ("2927408", "aaaaaaaa", "bbbbbbbb", "nome.real", "example.gov.br"):
        assert segredo not in serializado


def test_amostra_saudavel_agrega_sem_vazar_token_ou_identidade():
    respostas = {
        "/health": {"ok": True},
        "/ready": {"ok": True},
        "/ambiente": {
            "ambiente": "producao", "controles_ativos": True,
            "configuracao_valida": True,
        },
        "/admin/licencas": {"licencas": [{
            "status": "ativa", "codigo_ibge": "2927408",
            "expira_em": "2027-09-17T00:00:00+00:00",
        }]},
        "/admin/tentativas?limite=500": {"tentativas": [{
            "status": "ativa", "instalacao_id": "segredo-instalacao",
        }]},
        "/admin/auditoria?limite=500": {"eventos": [{
            "acao": "PATCH", "alvo": "/admin/licencas/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "resultado": "200", "operador": "fred",
        }]},
    }

    def falso(url, headers, timeout):
        del timeout
        assert "Bearer token-super-secreto" in headers.get("Authorization", "") or not headers
        caminho = url[url.index("/", len("https://")):]
        return 200, respostas[caminho], 12

    amostra = monitor.coletar_amostra(
        "https://licenca.example", "https://sicof.example",
        token_admin="token-super-secreto", operador="fred",
        latencia_alerta_ms=2000, dias_alerta_expiracao=30,
        requisitar=falso,
        agora=datetime(2026, 9, 17, tzinfo=timezone.utc),
    )
    assert amostra["possui_alerta_critico"] is False
    assert amostra["administracao"]["consultas_por_status"] == {"ativa": 1}
    serializado = json.dumps(amostra, sort_keys=True)
    for segredo in ("token-super-secreto", "2927408", "segredo-instalacao", "fred", "aaaaaaaa"):
        assert segredo not in serializado


def test_prontidao_indisponivel_gera_interrupcao():
    def falso(url, headers, timeout):
        del headers, timeout
        if url.endswith("/ready"):
            return 503, {"ok": False}, 20
        if url.endswith("/ambiente"):
            return 200, {
                "ambiente": "producao", "controles_ativos": True,
                "configuracao_valida": True,
            }, 10
        return 200, {"ok": True}, 10

    amostra = monitor.coletar_amostra(
        "https://licenca.example", "https://sicof.example",
        token_admin="", operador="monitor-piloto",
        latencia_alerta_ms=2000, dias_alerta_expiracao=30,
        requisitar=falso,
    )
    resumo = monitor.resumir_amostras([amostra])
    assert amostra["possui_alerta_critico"] is True
    assert resumo["resultado"] == "interromper"
    assert resumo["amostras_com_alerta_critico"] == 1


def test_monitor_sem_token_nao_aprova_piloto_mesmo_com_endpoints_saudaveis():
    def falso(url, headers, timeout):
        del headers, timeout
        if url.endswith("/ambiente"):
            return 200, {
                "ambiente": "producao", "controles_ativos": True,
                "configuracao_valida": True,
            }, 10
        return 200, {"ok": True}, 10

    amostra = monitor.coletar_amostra(
        "https://licenca.example", "https://sicof.example",
        token_admin="", operador="monitor-piloto",
        latencia_alerta_ms=2000, dias_alerta_expiracao=30,
        requisitar=falso,
    )
    assert amostra["possui_alerta_critico"] is True
    assert {a["codigo"] for a in amostra["alertas"]} == {
        "monitoramento_admin_sem_token"
    }
