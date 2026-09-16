# -*- coding: utf-8 -*-
"""Camada de dados: licenças, instalações, revogações e tentativas.

Duas implementações da mesma interface:
- ``RepositorioMemoria`` — para testes e execução local sem banco.
- ``RepositorioPostgres`` — produção na Railway (``DATABASE_URL``). O import de
  SQLAlchemy é **preguiçoso** (dentro da classe), para que os testes rodem sem o
  pacote instalado.

O modelo contratual é aditivo: registros antigos da allowlist continuam sem
``licenca_id`` até a migração da Etapa 4. Registros novos ligam uma instalação
a uma licença com vigência e limites próprios.
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
    def reativar_municipio(self, codigo_ibge: str) -> None: ...
    def listar_instalacoes(self) -> list[dict[str, Any]]: ...
    def listar_municipios_revogados(self) -> list[str]: ...
    def registrar_tentativa(self, instalacao_id: str, codigo_ibge: str,
                            status: str, quando: datetime) -> None: ...
    def listar_tentativas(self, limite: int = 100) -> list[dict[str, Any]]: ...
    def criar_licenca(self, dados: dict[str, Any]) -> dict[str, Any]: ...
    def obter_licenca(self, licenca_id: str) -> Optional[dict[str, Any]]: ...
    def listar_licencas(self) -> list[dict[str, Any]]: ...
    def atualizar_licenca(self, licenca_id: str,
                          dados: dict[str, Any]) -> Optional[dict[str, Any]]: ...
    def associar_instalacao(self, instalacao_id: str, licenca_id: str,
                            status: str, quando: datetime) -> dict[str, Any]: ...
    def contar_instalacoes_ativas(self, licenca_id: str) -> int: ...
    def registrar_evento_licenca(self, licenca_id: str, acao: str,
                                 detalhes: str, quando: datetime) -> None: ...
    def listar_eventos_licenca(self, licenca_id: str,
                               limite: int = 100) -> list[dict[str, Any]]: ...
    def pronto(self) -> bool: ...


# ─── Memória (testes / local) ────────────────────────────────────────────────


class RepositorioMemoria:
    def __init__(self) -> None:
        self._inst: dict[str, dict[str, Any]] = {}
        self._licencas: dict[str, dict[str, Any]] = {}
        self._eventos: list[dict[str, Any]] = []
        self._munic_revogados: set[str] = set()
        self.tentativas: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def obter_instalacao(self, instalacao_id: str) -> Optional[dict[str, Any]]:
        reg = self._inst.get(instalacao_id)
        return dict(reg) if reg else None

    def municipio_revogado(self, codigo_ibge: str) -> bool:
        return codigo_ibge in self._munic_revogados

    def autorizar(self, instalacao_id, codigo_ibge, max_usuarios) -> None:
        with self._lock:
            atual = self._inst.get(instalacao_id) or {}
            if atual.get("licenca_id"):
                raise ValueError("instalação contratual deve ser alterada pela licença vinculada")
            self._inst[instalacao_id] = {
                "instalacao_id": instalacao_id,
                "codigo_ibge": codigo_ibge,
                "max_usuarios": max_usuarios,
                "revogada": False,
                "licenca_id": None,
                "status_instalacao": None,
            }

    def revogar_instalacao(self, instalacao_id) -> bool:
        with self._lock:
            reg = self._inst.get(instalacao_id)
            if not reg:
                return False
            reg["revogada"] = True
            if reg.get("licenca_id"):
                reg["status_instalacao"] = "revogada"
                reg["revogada_em"] = datetime.now().astimezone().isoformat()
            return True

    def revogar_municipio(self, codigo_ibge) -> None:
        with self._lock:
            self._munic_revogados.add(codigo_ibge)

    def reativar_municipio(self, codigo_ibge) -> None:
        with self._lock:
            self._munic_revogados.discard(codigo_ibge)

    def listar_instalacoes(self) -> list[dict[str, Any]]:
        return [dict(v) for v in self._inst.values()]

    def listar_municipios_revogados(self) -> list[str]:
        return sorted(self._munic_revogados)

    def registrar_tentativa(self, instalacao_id, codigo_ibge, status, quando) -> None:
        # Sem dado pessoal: só identificador de instalação, município, status, horário.
        self.tentativas.append({
            "instalacao_id": instalacao_id, "codigo_ibge": codigo_ibge,
            "status": status, "quando": quando.isoformat(),
        })
        with self._lock:
            if instalacao_id in self._inst:
                self._inst[instalacao_id]["ultima_consulta_em"] = quando.isoformat()

    def listar_tentativas(self, limite: int = 100) -> list[dict[str, Any]]:
        return list(reversed(self.tentativas))[: max(1, int(limite))]

    def criar_licenca(self, dados: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            licenca_id = str(dados["licenca_id"])
            if licenca_id in self._licencas:
                raise ValueError("licença já existe")
            if any(
                reg.get("codigo_ibge") == dados.get("codigo_ibge")
                and reg.get("status") in {"ativa", "suspensa", "pendente"}
                for reg in self._licencas.values()
            ):
                raise ValueError(
                    "o município já possui uma licença em aberto; renove ou altere a existente"
                )
            self._licencas[licenca_id] = dict(dados)
            return dict(self._licencas[licenca_id])

    def obter_licenca(self, licenca_id: str) -> Optional[dict[str, Any]]:
        reg = self._licencas.get(licenca_id)
        return dict(reg) if reg else None

    def listar_licencas(self) -> list[dict[str, Any]]:
        return [
            {**dict(v), "instalacoes_ativas": self.contar_instalacoes_ativas(k)}
            for k, v in sorted(self._licencas.items(), key=lambda item: item[1]["criada_em"])
        ]

    def atualizar_licenca(self, licenca_id: str,
                          dados: dict[str, Any]) -> Optional[dict[str, Any]]:
        with self._lock:
            if licenca_id not in self._licencas:
                return None
            ativas = self.contar_instalacoes_ativas(licenca_id)
            if int(dados["max_instalacoes_ativas"]) < ativas:
                raise ValueError(
                    "o limite não pode ser menor que a quantidade de instalações ativas"
                )
            self._licencas[licenca_id] = dict(dados)
            return dict(self._licencas[licenca_id])

    def associar_instalacao(self, instalacao_id: str, licenca_id: str,
                            status: str, quando: datetime) -> dict[str, Any]:
        with self._lock:
            licenca = self._licencas.get(licenca_id)
            if licenca is None:
                raise ValueError("licença não encontrada")
            atual = self._inst.get(instalacao_id)
            if atual and atual.get("licenca_id") not in (None, licenca_id):
                raise ValueError("instalação já pertence a outra licença")
            if status == "ativa":
                outras = sum(
                    1 for iid, reg in self._inst.items()
                    if iid != instalacao_id and reg.get("licenca_id") == licenca_id
                    and reg.get("status_instalacao") == "ativa"
                )
                if outras >= int(licenca["max_instalacoes_ativas"]):
                    raise ValueError("limite de instalações ativas da licença atingido")
            reg = {
                "instalacao_id": instalacao_id,
                "licenca_id": licenca_id,
                "codigo_ibge": licenca["codigo_ibge"],
                "max_usuarios": None,
                "revogada": status in {"revogada", "substituida"},
                "status_instalacao": status,
                "ativada_em": quando.isoformat() if status == "ativa" else None,
                "revogada_em": quando.isoformat() if status in {"revogada", "substituida"} else None,
                "ultima_consulta_em": (atual or {}).get("ultima_consulta_em"),
            }
            self._inst[instalacao_id] = reg
            return dict(reg)

    def contar_instalacoes_ativas(self, licenca_id: str) -> int:
        return sum(
            1 for reg in self._inst.values()
            if reg.get("licenca_id") == licenca_id and reg.get("status_instalacao") == "ativa"
        )

    def registrar_evento_licenca(self, licenca_id: str, acao: str,
                                 detalhes: str, quando: datetime) -> None:
        self._eventos.append({
            "licenca_id": licenca_id, "acao": acao,
            "detalhes": detalhes, "quando": quando.isoformat(),
        })

    def listar_eventos_licenca(self, licenca_id: str,
                               limite: int = 100) -> list[dict[str, Any]]:
        eventos = [e for e in reversed(self._eventos) if e["licenca_id"] == licenca_id]
        return [dict(e) for e in eventos[: max(1, int(limite))]]

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
        ]
        with self._engine.begin() as con:
            for stmt in ddl:
                con.execute(text(stmt))

    def obter_instalacao(self, instalacao_id: str) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(
                text("SELECT instalacao_id, codigo_ibge, max_usuarios, revogada, "
                     "licenca_id, status_instalacao, ativada_em, revogada_em, ultima_consulta_em "
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
            vinculada = con.execute(
                text("SELECT licenca_id FROM instalacoes WHERE instalacao_id = :i"),
                {"i": instalacao_id},
            ).scalar_one_or_none()
            if vinculada:
                raise ValueError("instalação contratual deve ser alterada pela licença vinculada")
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
                text("UPDATE instalacoes SET revogada = TRUE, "
                     "status_instalacao = CASE WHEN licenca_id IS NULL THEN status_instalacao ELSE 'revogada' END, "
                     "revogada_em = CASE WHEN licenca_id IS NULL THEN revogada_em ELSE now() END "
                     "WHERE instalacao_id = :i"),
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

    def reativar_municipio(self, codigo_ibge) -> None:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(
                text("DELETE FROM municipios_revogados WHERE codigo_ibge = :c"),
                {"c": codigo_ibge},
            )

    def listar_instalacoes(self) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(
                text("SELECT instalacao_id, codigo_ibge, max_usuarios, revogada, "
                     "licenca_id, status_instalacao, ativada_em, revogada_em, ultima_consulta_em "
                     "FROM instalacoes ORDER BY criada_em")
            ).mappings().all()
        return [dict(r) for r in rows]

    def listar_municipios_revogados(self) -> list[str]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(
                text("SELECT codigo_ibge FROM municipios_revogados ORDER BY codigo_ibge")
            ).all()
        return [r[0] for r in rows]

    def registrar_tentativa(self, instalacao_id, codigo_ibge, status, quando) -> None:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(
                text("INSERT INTO tentativas (instalacao_id, codigo_ibge, status, quando) "
                     "VALUES (:i, :c, :s, :q)"),
                {"i": instalacao_id, "c": codigo_ibge, "s": status, "q": quando},
            )
            con.execute(
                text("UPDATE instalacoes SET ultima_consulta_em = :q WHERE instalacao_id = :i"),
                {"i": instalacao_id, "q": quando},
            )

    def listar_tentativas(self, limite: int = 100) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(
                text("SELECT instalacao_id, codigo_ibge, status, quando "
                     "FROM tentativas ORDER BY quando DESC LIMIT :n"),
                {"n": max(1, int(limite))},
            ).mappings().all()
        return [{**r, "quando": r["quando"].isoformat() if hasattr(r["quando"], "isoformat")
                 else str(r["quando"])} for r in rows]

    @staticmethod
    def _datas_iso(reg: dict[str, Any]) -> dict[str, Any]:
        return {
            k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in reg.items()
        }

    def criar_licenca(self, dados: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:c))"),
                {"c": dados["codigo_ibge"]},
            )
            aberta = con.execute(text(
                "SELECT 1 FROM licencas WHERE codigo_ibge=:c "
                "AND status IN ('ativa','suspensa','pendente')"
            ), {"c": dados["codigo_ibge"]}).first()
            if aberta:
                raise ValueError(
                    "o município já possui uma licença em aberto; renove ou altere a existente"
                )
            con.execute(text("""INSERT INTO licencas (
                licenca_id, codigo_ibge, status, inicio_em, expira_em,
                max_auditores, max_instalacoes_ativas, dias_offline,
                versao_minima, criada_em, atualizada_em
            ) VALUES (
                :licenca_id, :codigo_ibge, :status, :inicio_em, :expira_em,
                :max_auditores, :max_instalacoes_ativas, :dias_offline,
                :versao_minima, :criada_em, :atualizada_em
            )"""), dados)
        return self.obter_licenca(str(dados["licenca_id"])) or dict(dados)

    def obter_licenca(self, licenca_id: str) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(text(
                "SELECT licenca_id, codigo_ibge, status, inicio_em, expira_em, "
                "max_auditores, max_instalacoes_ativas, dias_offline, versao_minima, "
                "criada_em, atualizada_em FROM licencas WHERE licenca_id = :l"
            ), {"l": licenca_id}).mappings().first()
        return self._datas_iso(dict(row)) if row else None

    def listar_licencas(self) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(text("""SELECT l.licenca_id, l.codigo_ibge, l.status,
                l.inicio_em, l.expira_em, l.max_auditores, l.max_instalacoes_ativas,
                l.dias_offline, l.versao_minima, l.criada_em, l.atualizada_em,
                COUNT(i.instalacao_id) FILTER (WHERE i.status_instalacao = 'ativa') AS instalacoes_ativas
                FROM licencas l LEFT JOIN instalacoes i ON i.licenca_id = l.licenca_id
                GROUP BY l.licenca_id ORDER BY l.criada_em""")).mappings().all()
        return [self._datas_iso(dict(r)) for r in rows]

    def atualizar_licenca(self, licenca_id: str,
                          dados: dict[str, Any]) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:l))"), {"l": licenca_id})
            ativas = con.execute(text(
                "SELECT count(*) FROM instalacoes WHERE licenca_id=:l "
                "AND status_instalacao='ativa'"
            ), {"l": licenca_id}).scalar_one()
            if int(dados["max_instalacoes_ativas"]) < int(ativas):
                raise ValueError(
                    "o limite não pode ser menor que a quantidade de instalações ativas"
                )
            res = con.execute(text("""UPDATE licencas SET
                status=:status, inicio_em=:inicio_em, expira_em=:expira_em,
                max_auditores=:max_auditores,
                max_instalacoes_ativas=:max_instalacoes_ativas,
                dias_offline=:dias_offline, versao_minima=:versao_minima,
                atualizada_em=:atualizada_em
                WHERE licenca_id=:licenca_id"""), {**dados, "licenca_id": licenca_id})
        return self.obter_licenca(licenca_id) if (res.rowcount or 0) else None

    def associar_instalacao(self, instalacao_id: str, licenca_id: str,
                            status: str, quando: datetime) -> dict[str, Any]:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:l))"), {"l": licenca_id})
            licenca = con.execute(text(
                "SELECT codigo_ibge, max_instalacoes_ativas FROM licencas WHERE licenca_id=:l"
            ), {"l": licenca_id}).mappings().first()
            if licenca is None:
                raise ValueError("licença não encontrada")
            outra = con.execute(text(
                "SELECT licenca_id FROM instalacoes WHERE instalacao_id=:i"
            ), {"i": instalacao_id}).scalar_one_or_none()
            if outra not in (None, licenca_id):
                raise ValueError("instalação já pertence a outra licença")
            if status == "ativa":
                ativas = con.execute(text(
                    "SELECT count(*) FROM instalacoes WHERE licenca_id=:l "
                    "AND status_instalacao='ativa' AND instalacao_id<>:i"
                ), {"l": licenca_id, "i": instalacao_id}).scalar_one()
                if int(ativas) >= int(licenca["max_instalacoes_ativas"]):
                    raise ValueError("limite de instalações ativas da licença atingido")
            con.execute(text("""INSERT INTO instalacoes (
                instalacao_id, codigo_ibge, max_usuarios, revogada, licenca_id,
                status_instalacao, ativada_em, revogada_em
            ) VALUES (:i, :c, NULL, :r, :l, :s, :a, :v)
            ON CONFLICT (instalacao_id) DO UPDATE SET
                codigo_ibge=EXCLUDED.codigo_ibge, max_usuarios=NULL,
                revogada=EXCLUDED.revogada, licenca_id=EXCLUDED.licenca_id,
                status_instalacao=EXCLUDED.status_instalacao,
                ativada_em=EXCLUDED.ativada_em, revogada_em=EXCLUDED.revogada_em"""), {
                "i": instalacao_id, "c": licenca["codigo_ibge"], "l": licenca_id,
                "s": status, "r": status in {"revogada", "substituida"},
                "a": quando if status == "ativa" else None,
                "v": quando if status in {"revogada", "substituida"} else None,
            })
        return self.obter_instalacao(instalacao_id) or {}

    def contar_instalacoes_ativas(self, licenca_id: str) -> int:
        from sqlalchemy import text
        with self._engine.connect() as con:
            return int(con.execute(text(
                "SELECT count(*) FROM instalacoes WHERE licenca_id=:l AND status_instalacao='ativa'"
            ), {"l": licenca_id}).scalar_one())

    def registrar_evento_licenca(self, licenca_id: str, acao: str,
                                 detalhes: str, quando: datetime) -> None:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(text("INSERT INTO eventos_licenca (licenca_id, acao, detalhes, quando) "
                             "VALUES (:l, :a, :d, :q)"),
                        {"l": licenca_id, "a": acao, "d": detalhes, "q": quando})

    def listar_eventos_licenca(self, licenca_id: str,
                               limite: int = 100) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(text(
                "SELECT licenca_id, acao, detalhes, quando FROM eventos_licenca "
                "WHERE licenca_id=:l ORDER BY quando DESC LIMIT :n"
            ), {"l": licenca_id, "n": max(1, int(limite))}).mappings().all()
        return [self._datas_iso(dict(r)) for r in rows]

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
