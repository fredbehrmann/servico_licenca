# -*- coding: utf-8 -*-
"""Migrações versionadas e aditivas do esquema PostgreSQL.

Cada versão roda em sua própria transação e só é registrada depois que todos os
comandos terminam. Os comandos são idempotentes para também reconhecer bancos
criados pelas versões anteriores do serviço, quando ainda não existia a tabela
de controle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class MigracaoSchema:
    versao: int
    nome: str
    comandos: tuple[str, ...]

    @property
    def checksum(self) -> str:
        bruto = "\n-- comando --\n".join(self.comandos).encode("utf-8")
        return hashlib.sha256(bruto).hexdigest()


MIGRACOES = (
    MigracaoSchema(1, "estrutura_legada", (
        """CREATE TABLE IF NOT EXISTS instalacoes (
               instalacao_id TEXT PRIMARY KEY,
               codigo_ibge   TEXT NOT NULL,
               max_usuarios  INTEGER,
               revogada      BOOLEAN NOT NULL DEFAULT FALSE,
               criada_em     TIMESTAMPTZ NOT NULL DEFAULT now()
           )""",
        """CREATE TABLE IF NOT EXISTS municipios_revogados (
               codigo_ibge TEXT PRIMARY KEY,
               revogado_em TIMESTAMPTZ NOT NULL DEFAULT now()
           )""",
        """CREATE TABLE IF NOT EXISTS tentativas (
               id            BIGSERIAL PRIMARY KEY,
               instalacao_id TEXT NOT NULL,
               codigo_ibge   TEXT,
               status        TEXT NOT NULL,
               quando        TIMESTAMPTZ NOT NULL
           )""",
    )),
    MigracaoSchema(2, "modelo_contratual", (
        """CREATE TABLE IF NOT EXISTS licencas (
               licenca_id                 TEXT PRIMARY KEY,
               codigo_ibge                TEXT NOT NULL,
               status                     TEXT NOT NULL,
               inicio_em                  TIMESTAMPTZ NOT NULL,
               expira_em                  TIMESTAMPTZ NOT NULL,
               max_auditores              INTEGER NOT NULL,
               max_instalacoes_ativas     INTEGER NOT NULL DEFAULT 1,
               dias_offline               INTEGER NOT NULL DEFAULT 7,
               versao_minima              TEXT NOT NULL DEFAULT '1.0.0',
               criada_em                  TIMESTAMPTZ NOT NULL DEFAULT now(),
               atualizada_em              TIMESTAMPTZ NOT NULL DEFAULT now()
           )""",
        """CREATE TABLE IF NOT EXISTS eventos_licenca (
               id          BIGSERIAL PRIMARY KEY,
               licenca_id  TEXT NOT NULL,
               acao        TEXT NOT NULL,
               detalhes    TEXT NOT NULL DEFAULT '',
               quando      TIMESTAMPTZ NOT NULL
           )""",
        "ALTER TABLE instalacoes ADD COLUMN IF NOT EXISTS licenca_id TEXT REFERENCES licencas(licenca_id)",
        "ALTER TABLE instalacoes ADD COLUMN IF NOT EXISTS status_instalacao TEXT",
        "ALTER TABLE instalacoes ADD COLUMN IF NOT EXISTS ativada_em TIMESTAMPTZ",
        "ALTER TABLE instalacoes ADD COLUMN IF NOT EXISTS revogada_em TIMESTAMPTZ",
        "ALTER TABLE instalacoes ADD COLUMN IF NOT EXISTS ultima_consulta_em TIMESTAMPTZ",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_licenca_municipio_em_aberto "
        "ON licencas(codigo_ibge) WHERE status IN ('ativa', 'suspensa', 'pendente')",
    )),
    MigracaoSchema(3, "suporte_migracao_legada", (
        # Pendentes/revogadas vindas do legado podem não ter datas ou limite:
        # a migração não inventa contrato. Estados utilizáveis continuam
        # protegidos pela validação da aplicação e pelas conferências finais.
        "ALTER TABLE licencas ALTER COLUMN inicio_em DROP NOT NULL",
        "ALTER TABLE licencas ALTER COLUMN expira_em DROP NOT NULL",
        "ALTER TABLE licencas ALTER COLUMN max_auditores DROP NOT NULL",
        "ALTER TABLE licencas ADD COLUMN IF NOT EXISTS chave_migracao TEXT",
        "ALTER TABLE licencas ADD COLUMN IF NOT EXISTS migrada_em TIMESTAMPTZ",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_licencas_chave_migracao "
        "ON licencas(chave_migracao) WHERE chave_migracao IS NOT NULL",
        """CREATE TABLE IF NOT EXISTS migracoes_dados (
               migracao_id       TEXT PRIMARY KEY,
               fingerprint       TEXT NOT NULL,
               backup_referencia TEXT NOT NULL,
               aplicada_em       TIMESTAMPTZ NOT NULL,
               resultado         TEXT NOT NULL
           )""",
    )),
)


def executar_migracoes(engine) -> list[int]:
    """Aplica versões ausentes e devolve as versões aplicadas nesta chamada."""
    from sqlalchemy import text

    with engine.begin() as con:
        con.execute(text("""CREATE TABLE IF NOT EXISTS migracoes_schema (
            versao INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            checksum TEXT NOT NULL,
            aplicada_em TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""))

    aplicadas: list[int] = []
    for migracao in MIGRACOES:
        with engine.begin() as con:
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext('techfisco-schema'))"))
            existente = con.execute(text(
                "SELECT checksum FROM migracoes_schema WHERE versao=:v"
            ), {"v": migracao.versao}).scalar_one_or_none()
            if existente is not None:
                if str(existente) != migracao.checksum:
                    raise RuntimeError(
                        f"migração de esquema {migracao.versao} foi alterada depois de aplicada"
                    )
                continue
            for comando in migracao.comandos:
                con.execute(text(comando))
            con.execute(text(
                "INSERT INTO migracoes_schema (versao, nome, checksum) VALUES (:v, :n, :c)"
            ), {"v": migracao.versao, "n": migracao.nome, "c": migracao.checksum})
            aplicadas.append(migracao.versao)
    return aplicadas
