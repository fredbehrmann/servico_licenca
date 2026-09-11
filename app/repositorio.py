# -*- coding: utf-8 -*-
"""Camada de dados: allowlist, revogações e registro de tentativas.

Duas implementações da mesma interface:
- ``RepositorioMemoria`` — para testes e execução local sem banco.
- ``RepositorioPostgres`` — produção na Railway (``DATABASE_URL``). O import de
  SQLAlchemy é **preguiçoso** (dentro da classe), para que os testes rodem sem o
  pacote instalado.

Modelo de estado de uma instalação, decidido na pergunta 4:
- não está na allowlist            → ``instalacao_nao_autorizada``
- está e (revogada OU município revogado) → ``revogada``
- está e não revogada              → ``ativa``
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Optional, Protocol


class Repositorio(Protocol):
    def obter_instalacao(self, instalacao_id: str) -> Optional[dict[str, Any]]: ...
    def municipio_revogado(self, codigo_ibge: str) -> bool: ...
    def autorizar(self, instalacao_id: str, codigo_ibge: str,
                  max_usuarios: Optional[int]) -> None: ...
    def revogar_instalacao(self, instalacao_id: str) -> bool: ...
    def revogar_municipio(self, codigo_ibge: str) -> None: ...
    def listar_instalacoes(self) -> list[dict[str, Any]]: ...
    def registrar_tentativa(self, instalacao_id: str, codigo_ibge: str,
                            status: str, quando: datetime) -> None: ...
    def pronto(self) -> bool: ...


# ─── Memória (testes / local) ────────────────────────────────────────────────


class RepositorioMemoria:
    def __init__(self) -> None:
        self._inst: dict[str, dict[str, Any]] = {}
        self._munic_revogados: set[str] = set()
        self.tentativas: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def obter_instalacao(self, instalacao_id: str) -> Optional[dict[str, Any]]:
        reg = self._inst.get(instalacao_id)
        return dict(reg) if reg else None

    def municipio_revogado(self, codigo_ibge: str) -> bool:
        return codigo_ibge in self._munic_revogados

    def autorizar(self, instalacao_id, codigo_ibge, max_usuarios) -> None:
        with self._lock:
            self._inst[instalacao_id] = {
                "instalacao_id": instalacao_id,
                "codigo_ibge": codigo_ibge,
                "max_usuarios": max_usuarios,
                "revogada": False,
            }

    def revogar_instalacao(self, instalacao_id) -> bool:
        with self._lock:
            reg = self._inst.get(instalacao_id)
            if not reg:
                return False
            reg["revogada"] = True
            return True

    def revogar_municipio(self, codigo_ibge) -> None:
        with self._lock:
            self._munic_revogados.add(codigo_ibge)

    def listar_instalacoes(self) -> list[dict[str, Any]]:
        return [dict(v) for v in self._inst.values()]

    def registrar_tentativa(self, instalacao_id, codigo_ibge, status, quando) -> None:
        # Sem dado pessoal: só identificador de instalação, município, status, horário.
        self.tentativas.append({
            "instalacao_id": instalacao_id, "codigo_ibge": codigo_ibge,
            "status": status, "quando": quando.isoformat(),
        })

    def pronto(self) -> bool:
        return True


# ─── Postgres (produção) ─────────────────────────────────────────────────────


class RepositorioPostgres:
    """Implementação Postgres. SQLAlchemy é importado só aqui, sob demanda."""

    def __init__(self, database_url: str) -> None:
        from sqlalchemy import create_engine
        # Railway entrega postgres://; SQLAlchemy 2.x quer postgresql+psycopg://.
        url = database_url
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self._engine = create_engine(url, pool_pre_ping=True)
        self._criar_esquema()

    def _criar_esquema(self) -> None:
        from sqlalchemy import text
        ddl = [
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
        ]
        with self._engine.begin() as con:
            for stmt in ddl:
                con.execute(text(stmt))

    def obter_instalacao(self, instalacao_id: str) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(
                text("SELECT instalacao_id, codigo_ibge, max_usuarios, revogada "
                     "FROM instalacoes WHERE instalacao_id = :i"),
                {"i": instalacao_id},
            ).mappings().first()
        return dict(row) if row else None

    def municipio_revogado(self, codigo_ibge: str) -> bool:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(
                text("SELECT 1 FROM municipios_revogados WHERE codigo_ibge = :c"),
                {"c": codigo_ibge},
            ).first()
        return row is not None

    def autorizar(self, instalacao_id, codigo_ibge, max_usuarios) -> None:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(
                text("""INSERT INTO instalacoes (instalacao_id, codigo_ibge, max_usuarios, revogada)
                        VALUES (:i, :c, :m, FALSE)
                        ON CONFLICT (instalacao_id) DO UPDATE
                          SET codigo_ibge = EXCLUDED.codigo_ibge,
                              max_usuarios = EXCLUDED.max_usuarios,
                              revogada = FALSE"""),
                {"i": instalacao_id, "c": codigo_ibge, "m": max_usuarios},
            )

    def revogar_instalacao(self, instalacao_id) -> bool:
        from sqlalchemy import text
        with self._engine.begin() as con:
            res = con.execute(
                text("UPDATE instalacoes SET revogada = TRUE WHERE instalacao_id = :i"),
                {"i": instalacao_id},
            )
        return (res.rowcount or 0) > 0

    def revogar_municipio(self, codigo_ibge) -> None:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(
                text("INSERT INTO municipios_revogados (codigo_ibge) VALUES (:c) "
                     "ON CONFLICT (codigo_ibge) DO NOTHING"),
                {"c": codigo_ibge},
            )

    def listar_instalacoes(self) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(
                text("SELECT instalacao_id, codigo_ibge, max_usuarios, revogada "
                     "FROM instalacoes ORDER BY criada_em")
            ).mappings().all()
        return [dict(r) for r in rows]

    def registrar_tentativa(self, instalacao_id, codigo_ibge, status, quando) -> None:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(
                text("INSERT INTO tentativas (instalacao_id, codigo_ibge, status, quando) "
                     "VALUES (:i, :c, :s, :q)"),
                {"i": instalacao_id, "c": codigo_ibge, "s": status, "q": quando},
            )

    def pronto(self) -> bool:
        from sqlalchemy import text
        try:
            with self._engine.connect() as con:
                con.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


def repositorio_do_ambiente(database_url: Optional[str]) -> Repositorio:
    if database_url and database_url.strip():
        return RepositorioPostgres(database_url.strip())
    return RepositorioMemoria()
