# -*- coding: utf-8 -*-
"""Integração real do repositório com um PostgreSQL descartável.

Defina ``TEST_DATABASE_URL`` para executar. A integração contínua fornece um
servidor exclusivo e esta suíte cria um banco temporário por cenário.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import migracoes_schema
from app.migracoes_schema import MigracaoSchema
from app.repositorio import RepositorioPostgres


_AGORA = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
_IBGE = "2927408"


def _url_sqlalchemy(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@pytest.fixture
def banco_postgres():
    base = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not base:
        pytest.skip("TEST_DATABASE_URL não configurada")
    admin_url = _url_sqlalchemy(base)
    admin = create_engine(admin_url, pool_pre_ping=True)
    criados: list[str] = []

    def criar() -> str:
        nome = "techfisco_teste_" + uuid.uuid4().hex
        assert re.fullmatch(r"[a-z0-9_]+", nome)
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as con:
            con.execute(text(f'CREATE DATABASE "{nome}"'))
        criados.append(nome)
        return make_url(admin_url).set(database=nome).render_as_string(hide_password=False)

    yield criar

    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as con:
        for nome in criados:
            con.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=:banco AND pid <> pg_backend_pid()"
            ), {"banco": nome})
            con.execute(text(f'DROP DATABASE IF EXISTS "{nome}"'))
    admin.dispose()


def _licenca(
    licenca_id: str | None = None,
    limite_instalacoes: int = 1,
    codigo_ibge: str = _IBGE,
    nome_municipio: str = "Salvador",
) -> dict:
    return {
        "licenca_id": licenca_id or str(uuid.uuid4()),
        "codigo_ibge": codigo_ibge,
        "nome_municipio": nome_municipio,
        "status": "ativa",
        "inicio_em": _AGORA - timedelta(days=1),
        "expira_em": _AGORA + timedelta(days=365),
        "max_auditores": 5,
        "max_instalacoes_ativas": limite_instalacoes,
        "dias_offline": 7,
        "versao_minima": "1.0.0",
        "criada_em": _AGORA,
        "atualizada_em": _AGORA,
    }


def test_postgres_cria_esquema_completo_em_banco_vazio(banco_postgres):
    url = banco_postgres()
    repo = RepositorioPostgres(url)
    try:
        with repo._engine.connect() as con:
            versoes = list(con.execute(text(
                "SELECT versao FROM migracoes_schema ORDER BY versao"
            )).scalars())
            tabelas = set(con.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )).scalars())
            colunas_licencas = set(con.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='licencas'"
            )).scalars())
            colunas_instalacoes = set(con.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='instalacoes'"
            )).scalars())
        assert versoes == [m.versao for m in migracoes_schema.MIGRACOES]
        assert {
            "licencas", "instalacoes", "tentativas", "eventos_licenca",
            "auditoria_admin", "migracoes_dados", "migracoes_schema",
        } <= tabelas
        assert "nome_municipio" in colunas_licencas
        assert "associada_em" in colunas_instalacoes
        assert repo.pronto() is True
    finally:
        repo._engine.dispose()


def test_postgres_migra_esquema_legado_sem_perder_instalacao(banco_postgres):
    url = banco_postgres()
    engine = create_engine(url)
    instalacao_id = str(uuid.uuid4())
    with engine.begin() as con:
        for comando in migracoes_schema.MIGRACOES[0].comandos:
            con.execute(text(comando))
        con.execute(text(
            "INSERT INTO instalacoes (instalacao_id, codigo_ibge, max_usuarios) "
            "VALUES (:i, :c, 4)"
        ), {"i": instalacao_id, "c": _IBGE})
    engine.dispose()

    repo = RepositorioPostgres(url)
    try:
        registro = repo.obter_instalacao(instalacao_id)
        assert registro is not None
        assert registro["codigo_ibge"] == _IBGE
        assert registro["max_usuarios"] == 4
        assert registro["licenca_id"] is None
        assert registro["associada_em"] is not None
        assert repo.pronto() is True
    finally:
        repo._engine.dispose()


def test_postgres_persiste_licenca_apos_reabrir_repositorio(banco_postgres):
    url = banco_postgres()
    licenca = _licenca()
    instalacao_id = str(uuid.uuid4())
    primeiro = RepositorioPostgres(url)
    primeiro.criar_licenca(licenca)
    primeiro.associar_instalacao(instalacao_id, licenca["licenca_id"], "ativa", _AGORA)
    primeiro._engine.dispose()

    segundo = RepositorioPostgres(url)
    try:
        assert segundo.obter_licenca(licenca["licenca_id"])["max_auditores"] == 5
        assert segundo.obter_licenca(licenca["licenca_id"])["nome_municipio"] == "Salvador"
        assert segundo.obter_instalacao(instalacao_id)["status_instalacao"] == "ativa"
        assert segundo.obter_instalacao(instalacao_id)["associada_em"] == _AGORA
        assert segundo.contar_instalacoes_ativas(licenca["licenca_id"]) == 1
    finally:
        segundo._engine.dispose()


def test_postgres_transfere_instalacao_ativa_para_licenca_de_outro_municipio(
    banco_postgres,
):
    url = banco_postgres()
    repo = RepositorioPostgres(url)
    anterior = repo.criar_licenca(_licenca())
    nova = repo.criar_licenca(_licenca(codigo_ibge="3550308", nome_municipio="São Paulo"))
    instalacao_id = str(uuid.uuid4())
    try:
        repo.associar_instalacao(instalacao_id, anterior["licenca_id"], "ativa", _AGORA)
        transferida = repo.associar_instalacao(
            instalacao_id, nova["licenca_id"], "ativa", _AGORA,
        )

        assert transferida["licenca_id"] == nova["licenca_id"]
        assert transferida["codigo_ibge"] == "3550308"
        assert repo.contar_instalacoes_ativas(anterior["licenca_id"]) == 0
        assert repo.contar_instalacoes_ativas(nova["licenca_id"]) == 1
    finally:
        repo._engine.dispose()


def test_postgres_concorrencia_nunca_ultrapassa_limite_de_instalacoes(banco_postgres):
    url = banco_postgres()
    repo = RepositorioPostgres(url)
    licenca = repo.criar_licenca(_licenca(limite_instalacoes=1))
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    barreira = threading.Barrier(2)

    def ativar(instalacao_id: str) -> str:
        barreira.wait(timeout=5)
        try:
            repo.associar_instalacao(instalacao_id, licenca["licenca_id"], "ativa", _AGORA)
            return "ativa"
        except ValueError as exc:
            assert "limite" in str(exc)
            return "recusada"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            resultados = list(executor.map(ativar, ids))
        assert sorted(resultados) == ["ativa", "recusada"]
        assert repo.contar_instalacoes_ativas(licenca["licenca_id"]) == 1

        ativa = next(i for i in ids if repo.obter_instalacao(i))
        outra = next(i for i in ids if i != ativa)
        barreira = threading.Barrier(2)

        def trocar(instalacao_id: str, status: str) -> str:
            barreira.wait(timeout=5)
            try:
                repo.associar_instalacao(
                    instalacao_id, licenca["licenca_id"], status, _AGORA,
                )
                return status
            except ValueError:
                return "recusada"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuros = [
                executor.submit(trocar, ativa, "revogada"),
                executor.submit(trocar, outra, "ativa"),
            ]
            [f.result(timeout=10) for f in futuros]
        assert repo.contar_instalacoes_ativas(licenca["licenca_id"]) <= 1
    finally:
        repo._engine.dispose()


def test_postgres_consultas_e_relatorios_administrativos(banco_postgres):
    url = banco_postgres()
    repo = RepositorioPostgres(url)
    licenca = repo.criar_licenca(_licenca())
    instalacao_id = str(uuid.uuid4())
    repo.associar_instalacao(instalacao_id, licenca["licenca_id"], "ativa", _AGORA)
    repo.registrar_tentativa(instalacao_id, _IBGE, "ativa", _AGORA)
    repo.registrar_evento_licenca(
        licenca["licenca_id"], "licenca_criada", json.dumps({"origem": "teste"}), _AGORA,
    )
    repo.registrar_auditoria_admin(
        "fred", "POST", "/admin/licencas", "200", "", _AGORA,
    )
    try:
        assert repo.listar_licencas()[0]["instalacoes_ativas"] == 1
        assert repo.listar_instalacoes()[0]["instalacao_id"] == instalacao_id
        assert repo.listar_tentativas()[0]["status"] == "ativa"
        assert repo.listar_eventos_licenca(licenca["licenca_id"])[0]["acao"] == "licenca_criada"
        assert repo.listar_auditoria_admin()[0]["operador"] == "fred"
    finally:
        repo._engine.dispose()


def test_postgres_faz_rollback_da_migracao_que_falha(banco_postgres, monkeypatch):
    url = banco_postgres()
    engine = create_engine(url)
    falha = MigracaoSchema(999, "falha_controlada", (
        "CREATE TABLE tabela_que_deve_sumir (id INTEGER PRIMARY KEY)",
        "INSERT INTO tabela_inexistente (id) VALUES (1)",
    ))
    monkeypatch.setattr(
        migracoes_schema,
        "MIGRACOES",
        (*migracoes_schema.MIGRACOES, falha),
    )

    with pytest.raises(Exception):
        migracoes_schema.executar_migracoes(engine)

    with engine.connect() as con:
        tabela = con.execute(text(
            "SELECT to_regclass('public.tabela_que_deve_sumir')"
        )).scalar_one()
        versao = con.execute(text(
            "SELECT 1 FROM migracoes_schema WHERE versao=999"
        )).scalar_one_or_none()
    engine.dispose()
    assert tabela is None
    assert versao is None
