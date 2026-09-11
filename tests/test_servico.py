# -*- coding: utf-8 -*-
"""Decisão pura de status (sem assinatura, sem HTTP)."""

from __future__ import annotations

from app.repositorio import RepositorioMemoria
from app.servico import Config, decidir

_REQ = {"instalacao_id": "inst-1", "codigo_ibge": "2927408",
        "versao_app": "1.0.0", "nonce": "n-abc"}


def _repo_com_autorizada(max_usuarios=5):
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", "2927408", max_usuarios)
    return repo


def test_nao_autorizada_quando_ausente():
    r = decidir(_REQ, RepositorioMemoria(), Config())
    assert r["status"] == "instalacao_nao_autorizada"
    assert r["max_usuarios"] is None and r["offline_ate"] is None
    # vínculo e nonce ecoam mesmo na negação
    assert r["codigo_ibge"] == "2927408" and r["instalacao_id"] == "inst-1"
    assert r["nonce"] == "n-abc"


def test_ativa_quando_autorizada():
    r = decidir(_REQ, _repo_com_autorizada(5), Config(dias_offline=7))
    assert r["status"] == "ativa"
    assert r["max_usuarios"] == 5
    assert r["expira_em"] and r["offline_ate"]
    assert "assinatura" not in r          # decidir não assina


def test_revogada_por_instalacao():
    repo = _repo_com_autorizada()
    repo.revogar_instalacao("inst-1")
    r = decidir(_REQ, repo, Config())
    assert r["status"] == "revogada"
    assert r["offline_ate"] is None       # revogada não recebe janela off-line


def test_revogada_por_municipio():
    repo = _repo_com_autorizada()
    repo.revogar_municipio("2927408")
    r = decidir(_REQ, repo, Config())
    assert r["status"] == "revogada"


def test_config_carimba_key_id_e_ambiente():
    r = decidir(_REQ, _repo_com_autorizada(), Config(key_id="prod-ed25519-v1", ambiente="producao"))
    assert r["key_id"] == "prod-ed25519-v1" and r["ambiente"] == "producao"
