# -*- coding: utf-8 -*-
"""Etapa 4 — relatório, decisões e migração idempotente da allowlist."""

from __future__ import annotations

import stat
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app import licencas
from app.migracao_legado import (
    ErroMigracao,
    aplicar_documento,
    gerar_documento,
    salvar_documento,
    validar_documento,
)
from app.migracoes_schema import MIGRACOES
from app.repositorio import RepositorioMemoria
from app.servico import Config, decidir


_AGORA = datetime(2026, 9, 16, 15, tzinfo=timezone.utc)
_IBGE = "2927408"


def _decisao(documento, ibge=_IBGE):
    return next(d for d in documento["decisoes"] if d["codigo_ibge"] == ibge)


def _confirmar_ativa(documento, *, instalacao="inst-1", max_auditores=5):
    decisao = _decisao(documento)
    decisao.update({
        "confirmado": True,
        "status": "ativa",
        "inicio_em": (_AGORA - timedelta(days=30)).isoformat(),
        "expira_em": (_AGORA + timedelta(days=335)).isoformat(),
        "max_auditores": max_auditores,
        "instalacao_ativa_id": instalacao,
        "instalacoes_contingencia_ids": [
            iid for iid in decisao["instalacoes_contingencia_ids"] if iid != instalacao
        ],
    })
    return decisao


def test_relatorio_aponta_apenas_decisoes_que_nao_podem_ser_inventadas():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, None)
    repo.autorizar("inst-2", _IBGE, 8)
    repo.revogar_municipio(_IBGE)

    documento = gerar_documento(repo, agora=_AGORA)
    tipos = {item["tipo"] for item in documento["inconsistencias"]}
    assert {
        "limite_ausente_ou_invalido",
        "mais_de_uma_candidata_ativa",
        "municipio_revogado_com_instalacao_nao_revogada",
    }.issubset(tipos)
    decisao = _decisao(documento)
    assert decisao["status"] == "revogada"
    assert decisao["inicio_em"] is None and decisao["expira_em"] is None
    assert decisao["confirmado"] is False
    assert decisao["instalacoes_contingencia_ids"] == []


def test_migracao_ativa_preserva_dados_e_usa_vigencia_real():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, 5)
    documento = gerar_documento(repo, agora=_AGORA)
    _confirmar_ativa(documento)

    resultado = aplicar_documento(documento, repo, "backup-homologado-001", agora=_AGORA)
    assert resultado["total_instalacoes_antes"] == resultado["total_instalacoes_depois"] == 1
    instalacao = repo.obter_instalacao("inst-1")
    assert instalacao["max_usuarios"] == 5          # campo legado preservado para retorno
    assert instalacao["status_instalacao"] == "ativa"
    assert instalacao["licenca_id"]

    resposta = decidir({
        "instalacao_id": "inst-1", "codigo_ibge": _IBGE,
        "versao_app": "1.0.0", "nonce": "n",
    }, repo, Config(), _AGORA)
    assert resposta["status"] == "ativa" and resposta["max_usuarios"] == 5
    assert resposta["expira_em"] == (_AGORA + timedelta(days=335)).isoformat()


def test_sem_dados_contratuais_migra_como_pendente_sem_inventar():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, None)
    repo.autorizar("inst-rev", _IBGE, None)
    repo.revogar_instalacao("inst-rev")
    documento = gerar_documento(repo, agora=_AGORA)
    decisao = _decisao(documento)
    decisao["confirmado"] = True

    aplicar_documento(documento, repo, "backup-002", agora=_AGORA)
    licenca_id = repo.obter_instalacao("inst-1")["licenca_id"]
    contrato = repo.obter_licenca(licenca_id)
    assert contrato["status"] == "pendente"
    assert contrato["inicio_em"] is None and contrato["expira_em"] is None
    assert contrato["max_auditores"] is None
    assert repo.obter_instalacao("inst-1")["status_instalacao"] == "contingencia"
    assert repo.obter_instalacao("inst-rev")["status_instalacao"] == "revogada"


def test_execucao_repetida_nao_duplica_licenca_nem_evento():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, 5)
    documento = gerar_documento(repo, agora=_AGORA)
    _confirmar_ativa(documento)

    primeira = aplicar_documento(documento, repo, "backup-003", agora=_AGORA)
    eventos_antes = len(repo._eventos)
    segunda = aplicar_documento(documento, repo, "backup-003", agora=_AGORA)
    assert primeira["repetida"] is False and segunda["repetida"] is True
    assert len(repo.listar_licencas()) == 1
    assert len(repo._eventos) == eventos_antes
    assert repo.migracao_legado_concluida() is True


def test_fingerprint_impede_aplicar_decisao_sobre_cadastro_alterado():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, 5)
    documento = gerar_documento(repo, agora=_AGORA)
    _confirmar_ativa(documento)
    repo.autorizar("inst-nova", "3550308", 3)

    with pytest.raises(ErroMigracao, match="cadastro mudou"):
        aplicar_documento(documento, repo, "backup-004", agora=_AGORA)
    assert repo.obter_instalacao("inst-1")["licenca_id"] is None


def test_transacao_em_memoria_desfaz_lote_parcial_em_erro():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, 5)
    documento = gerar_documento(repo, agora=_AGORA)
    _confirmar_ativa(documento)
    from app.migracao_legado import preparar_planos
    planos = preparar_planos(documento, repo, agora=_AGORA)
    plano_ruim = deepcopy(planos[0])
    plano_ruim["licenca"]["licenca_id"] = "licenca-inexistente"
    plano_ruim["licenca"]["codigo_ibge"] = "3550308"
    plano_ruim["licenca"]["chave_migracao"] = "legado-v1:3550308"
    plano_ruim["instalacoes"] = [{"instalacao_id": "nao-existe", "status": "ativa"}]

    with pytest.raises(ValueError, match="desapareceu"):
        repo.aplicar_migracao_legada(
            "lote-com-erro", documento["fingerprint_origem"], "backup-005",
            [planos[0], plano_ruim], _AGORA,
        )
    assert repo.obter_instalacao("inst-1")["licenca_id"] is None
    assert repo.listar_licencas() == []


def test_reutiliza_licenca_existente_sem_criar_paralela():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, 5)
    existente = licencas.normalizar_criacao({
        "licenca_id": "contrato-existente", "codigo_ibge": _IBGE, "status": "ativa",
        "inicio_em": (_AGORA - timedelta(days=1)).isoformat(),
        "expira_em": (_AGORA + timedelta(days=100)).isoformat(),
        "max_auditores": 5, "max_instalacoes_ativas": 1,
        "dias_offline": 7, "versao_minima": "1.0.0",
    }, agora=_AGORA)
    repo.criar_licenca(existente)
    documento = gerar_documento(repo, agora=_AGORA)
    decisao = _decisao(documento)
    decisao["confirmado"] = True
    assert decisao["licenca_id_existente"] == "contrato-existente"

    aplicar_documento(documento, repo, "backup-006", agora=_AGORA)
    assert len(repo.listar_licencas()) == 1
    assert repo.obter_instalacao("inst-1")["licenca_id"] == "contrato-existente"


def test_licenca_pendente_pode_ser_completada_sem_perder_chave_de_migracao():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, None)
    documento = gerar_documento(repo, agora=_AGORA)
    _decisao(documento)["confirmado"] = True
    aplicar_documento(documento, repo, "backup-007", agora=_AGORA)
    licenca_id = repo.obter_instalacao("inst-1")["licenca_id"]
    pendente = repo.obter_licenca(licenca_id)

    completa = licencas.normalizar_atualizacao(pendente, {
        "status": "ativa",
        "inicio_em": (_AGORA - timedelta(days=1)).isoformat(),
        "expira_em": (_AGORA + timedelta(days=365)).isoformat(),
        "max_auditores": 5,
    }, agora=_AGORA)
    repo.atualizar_licenca(licenca_id, completa)
    assert repo.obter_licenca(licenca_id)["chave_migracao"] == f"legado-v1:{_IBGE}"


def test_backup_e_inconsistencia_impeditiva_sao_obrigatorios():
    repo = RepositorioMemoria()
    repo.autorizar("inst-1", _IBGE, 5)
    documento = gerar_documento(repo, agora=_AGORA)
    _confirmar_ativa(documento)
    with pytest.raises(ErroMigracao, match="backup"):
        aplicar_documento(documento, repo, "", agora=_AGORA)

    repo_invalido = RepositorioMemoria()
    repo_invalido.autorizar("sem-municipio", "", 5)
    invalido = gerar_documento(repo_invalido, agora=_AGORA)
    assert invalido["resumo"]["inconsistencias_impeditivas"] == 1
    with pytest.raises(ErroMigracao, match="impeditivas"):
        validar_documento(invalido, repo_invalido)


def test_arquivo_de_decisoes_tem_permissao_restrita(tmp_path):
    caminho = tmp_path / "decisoes.json"
    salvar_documento(caminho, {"teste": True})
    assert stat.S_IMODE(caminho.stat().st_mode) == 0o600


def test_migracoes_de_esquema_sao_versionadas_e_nao_removem_dados():
    assert [m.versao for m in MIGRACOES] == [1, 2, 3, 4, 5]
    assert len({m.checksum for m in MIGRACOES}) == len(MIGRACOES)
    comandos = "\n".join(c for m in MIGRACOES for c in m.comandos).upper()
    assert "DROP TABLE" not in comandos and "DROP COLUMN" not in comandos
