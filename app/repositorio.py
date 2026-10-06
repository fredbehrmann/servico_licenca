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

import copy
import hmac
import json
import threading
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol


class RecuperacaoDuplicada(ValueError):
    pass


class RecuperacaoEstadoInvalido(ValueError):
    pass


class RecuperacaoAutoaprovacao(ValueError):
    pass


def hmac_compare_texto(a: Any, b: Any) -> bool:
    return hmac.compare_digest(str(a or ""), str(b or ""))


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
    def registrar_auditoria_admin(self, operador: str, acao: str, alvo: str,
                                  resultado: str, detalhes: str,
                                  quando: datetime) -> None: ...
    def listar_auditoria_admin(self, limite: int = 100) -> list[dict[str, Any]]: ...
    def preparar_recuperacao(self, dados: dict[str, Any]) -> dict[str, Any]: ...
    def obter_recuperacao(self, request_id: str) -> Optional[dict[str, Any]]: ...
    def listar_recuperacoes(self, limite: int = 100,
                            estado: str = "") -> list[dict[str, Any]]: ...
    def aprovar_recuperacao(self, request_id: str, aprovador: str, quando: int,
                            dupla_aprovacao: bool = True) -> Optional[dict[str, Any]]: ...
    def confirmar_emissao_recuperacao(
        self, request_id: str, request_digest: str, emitido_por: str,
        kid: str, jti_digest: str, token_digest: str,
        emitido_em: int, token_expira_em: int,
    ) -> Optional[dict[str, Any]]: ...
    def contar_recuperacoes_recentes(self, operador: str, installation_id: str,
                                     desde: int) -> tuple[int, int]: ...
    def aplicar_retencao(self, agora: datetime, tentativas_dias: int,
                         auditoria_dias: int) -> dict[str, int]: ...
    def obter_migracao_dados(self, migracao_id: str) -> Optional[dict[str, Any]]: ...
    def migracao_legado_concluida(self) -> bool: ...
    def aplicar_migracao_legada(self, migracao_id: str, fingerprint: str,
                                backup_referencia: str, planos: list[dict[str, Any]],
                                quando: datetime) -> dict[str, Any]: ...
    def pronto(self) -> bool: ...


# ─── Memória (testes / local) ────────────────────────────────────────────────


class RepositorioMemoria:
    def __init__(self) -> None:
        self._inst: dict[str, dict[str, Any]] = {}
        self._licencas: dict[str, dict[str, Any]] = {}
        self._eventos: list[dict[str, Any]] = []
        self._auditoria_admin: list[dict[str, Any]] = []
        self._recuperacoes: dict[str, dict[str, Any]] = {}
        self._migracoes_dados: dict[str, dict[str, Any]] = {}
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
            self._licencas[licenca_id] = {**self._licencas[licenca_id], **dict(dados)}
            return dict(self._licencas[licenca_id])

    def associar_instalacao(self, instalacao_id: str, licenca_id: str,
                            status: str, quando: datetime) -> dict[str, Any]:
        with self._lock:
            licenca = self._licencas.get(licenca_id)
            if licenca is None:
                raise ValueError("licença não encontrada")
            atual = self._inst.get(instalacao_id)
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
                "associada_em": quando.isoformat(),
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

    def registrar_auditoria_admin(self, operador: str, acao: str, alvo: str,
                                  resultado: str, detalhes: str,
                                  quando: datetime) -> None:
        with self._lock:
            self._auditoria_admin.append({
                "operador": operador, "acao": acao, "alvo": alvo,
                "resultado": resultado, "detalhes": detalhes,
                "quando": quando.isoformat(),
            })

    def listar_auditoria_admin(self, limite: int = 100) -> list[dict[str, Any]]:
        return [dict(e) for e in reversed(self._auditoria_admin[-max(1, int(limite)):])]

    def preparar_recuperacao(self, dados: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            request_id = str(dados["request_id"])
            digest = str(dados["request_digest"])
            if request_id in self._recuperacoes or any(
                item.get("request_digest") == digest for item in self._recuperacoes.values()
            ):
                raise RecuperacaoDuplicada("Esta solicitação já foi registrada.")
            self._recuperacoes[request_id] = copy.deepcopy(dados)
            return copy.deepcopy(self._recuperacoes[request_id])

    def obter_recuperacao(self, request_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            item = self._recuperacoes.get(request_id)
            return copy.deepcopy(item) if item else None

    def listar_recuperacoes(self, limite: int = 100,
                            estado: str = "") -> list[dict[str, Any]]:
        with self._lock:
            itens = list(self._recuperacoes.values())
            if estado:
                itens = [item for item in itens if item.get("estado") == estado]
            itens.sort(key=lambda item: int(item.get("atualizado_em") or 0), reverse=True)
            return copy.deepcopy(itens[:max(1, int(limite))])

    def aprovar_recuperacao(self, request_id: str, aprovador: str, quando: int,
                            dupla_aprovacao: bool = True) -> Optional[dict[str, Any]]:
        with self._lock:
            item = self._recuperacoes.get(request_id)
            if item is None:
                return None
            if item.get("estado") != "preparada":
                raise RecuperacaoEstadoInvalido("A solicitação não está aguardando aprovação.")
            if int(item.get("solicitacao_expira_em") or 0) < int(quando):
                item["estado"] = "expirada"
                item["atualizado_em"] = int(quando)
                raise RecuperacaoEstadoInvalido("A solicitação expirou.")
            if dupla_aprovacao and hmac_compare_texto(item.get("operador_preparou"), aprovador):
                raise RecuperacaoAutoaprovacao("Quem preparou não pode aprovar a solicitação.")
            item["estado"] = "aprovada"
            item["aprovador"] = aprovador
            item["atualizado_em"] = int(quando)
            return copy.deepcopy(item)

    def confirmar_emissao_recuperacao(
        self, request_id: str, request_digest: str, emitido_por: str,
        kid: str, jti_digest: str, token_digest: str,
        emitido_em: int, token_expira_em: int,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            item = self._recuperacoes.get(request_id)
            if item is None:
                return None
            if item.get("estado") != "aprovada":
                raise RecuperacaoEstadoInvalido("A solicitação não está aprovada para emissão.")
            if not hmac_compare_texto(item.get("request_digest"), request_digest):
                raise RecuperacaoEstadoInvalido("A solicitação informada diverge da preparada.")
            if int(item.get("solicitacao_expira_em") or 0) < int(emitido_em):
                item["estado"] = "expirada"
                item["atualizado_em"] = int(emitido_em)
                raise RecuperacaoEstadoInvalido("A solicitação expirou.")
            item.update({
                "estado": "emitida",
                "emitido_por": emitido_por,
                "kid": kid,
                "jti_digest": jti_digest,
                "token_digest": token_digest,
                "emitido_em": int(emitido_em),
                "token_expira_em": int(token_expira_em),
                "atualizado_em": int(emitido_em),
            })
            return copy.deepcopy(item)

    def contar_recuperacoes_recentes(self, operador: str, installation_id: str,
                                     desde: int) -> tuple[int, int]:
        with self._lock:
            por_operador = sum(
                1 for item in self._recuperacoes.values()
                if item.get("operador_preparou") == operador
                and int(item.get("criado_em") or 0) >= int(desde)
            )
            por_instalacao = sum(
                1 for item in self._recuperacoes.values()
                if item.get("installation_id") == installation_id
                and int(item.get("criado_em") or 0) >= int(desde)
            )
            return por_operador, por_instalacao

    @staticmethod
    def _data_evento(valor: Any) -> datetime:
        return valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor))

    def aplicar_retencao(self, agora: datetime, tentativas_dias: int,
                         auditoria_dias: int) -> dict[str, int]:
        corte_tentativas = agora - timedelta(days=tentativas_dias)
        corte_auditoria = agora - timedelta(days=auditoria_dias)
        with self._lock:
            antes_t = len(self.tentativas)
            antes_e = len(self._eventos)
            antes_a = len(self._auditoria_admin)
            antes_r = len(self._recuperacoes)
            self.tentativas = [
                item for item in self.tentativas
                if self._data_evento(item["quando"]) >= corte_tentativas
            ]
            self._eventos = [
                item for item in self._eventos
                if self._data_evento(item["quando"]) >= corte_auditoria
            ]
            self._auditoria_admin = [
                item for item in self._auditoria_admin
                if self._data_evento(item["quando"]) >= corte_auditoria
            ]
            corte_epoch = int(corte_auditoria.timestamp())
            self._recuperacoes = {
                chave: item for chave, item in self._recuperacoes.items()
                if int(item.get("atualizado_em") or 0) >= corte_epoch
            }
        return {
            "tentativas": antes_t - len(self.tentativas),
            "eventos_licenca": antes_e - len(self._eventos),
            "auditoria_admin": antes_a - len(self._auditoria_admin),
            "recuperacoes_senha": antes_r - len(self._recuperacoes),
        }

    def obter_migracao_dados(self, migracao_id: str) -> Optional[dict[str, Any]]:
        reg = self._migracoes_dados.get(migracao_id)
        return copy.deepcopy(reg) if reg else None

    def migracao_legado_concluida(self) -> bool:
        return any(chave.startswith("legado-v1-") for chave in self._migracoes_dados)

    def aplicar_migracao_legada(self, migracao_id: str, fingerprint: str,
                                backup_referencia: str, planos: list[dict[str, Any]],
                                quando: datetime) -> dict[str, Any]:
        """Converte o lote inteiro sob um único lock; erro restaura o snapshot."""
        with self._lock:
            anterior = self._migracoes_dados.get(migracao_id)
            if anterior:
                return {**copy.deepcopy(anterior["resultado"]), "repetida": True}
            snapshot = (
                copy.deepcopy(self._inst), copy.deepcopy(self._licencas),
                copy.deepcopy(self._eventos), copy.deepcopy(self._migracoes_dados),
            )
            try:
                total_antes = len(self._inst)
                revogadas_antes = sum(1 for i in self._inst.values() if i.get("revogada"))
                municipios_antes = {str(i.get("codigo_ibge") or "") for i in self._inst.values()}
                for plano in planos:
                    licenca = copy.deepcopy(plano["licenca"])
                    licenca_id = str(licenca["licenca_id"])
                    if self.municipio_revogado(str(licenca["codigo_ibge"])) != bool(
                        plano.get("municipio_revogado_esperado")
                    ):
                        raise ValueError(
                            f"revogação municipal mudou durante a migração: {licenca['codigo_ibge']}"
                        )
                    existente = self._licencas.get(licenca_id)
                    if existente:
                        if existente.get("codigo_ibge") != licenca.get("codigo_ibge"):
                            raise ValueError(f"licença existente pertence a outro município: {licenca_id}")
                        chave_atual = existente.get("chave_migracao")
                        if chave_atual not in (None, licenca.get("chave_migracao")):
                            raise ValueError(f"colisão de licença migrada: {licenca_id}")
                        for campo in (
                            "status", "inicio_em", "expira_em", "max_auditores",
                            "max_instalacoes_ativas", "dias_offline", "versao_minima",
                        ):
                            if existente.get(campo) != licenca.get(campo):
                                raise ValueError(
                                    f"licença mudou durante a migração: {licenca_id} ({campo})"
                                )
                        existente["chave_migracao"] = licenca.get("chave_migracao")
                        existente["migrada_em"] = licenca.get("migrada_em")
                    else:
                        self._licencas[licenca_id] = licenca
                    for vinculo in plano["instalacoes"]:
                        iid = str(vinculo["instalacao_id"])
                        reg = self._inst.get(iid)
                        if reg is None:
                            raise ValueError(f"instalação desapareceu durante a migração: {iid}")
                        if (
                            str(reg.get("codigo_ibge") or "") != vinculo["codigo_ibge_esperado"]
                            or bool(reg.get("revogada")) != vinculo["revogada_esperada"]
                            or reg.get("max_usuarios") != vinculo["max_usuarios_esperado"]
                        ):
                            raise ValueError(f"instalação mudou durante a migração: {iid}")
                        if reg.get("licenca_id") not in (None, licenca_id):
                            raise ValueError(f"instalação já pertence a outra licença: {iid}")
                        reg["licenca_id"] = licenca_id
                        reg["status_instalacao"] = vinculo["status"]
                        reg["associada_em"] = quando.isoformat()
                        reg["ativada_em"] = quando.isoformat() if vinculo["status"] == "ativa" else None
                        reg["revogada_em"] = (
                            quando.isoformat() if vinculo["status"] in {"revogada", "substituida"}
                            else None
                        )
                    self._eventos.append({
                        "licenca_id": licenca_id, "acao": "migracao_legada",
                        "detalhes": json.dumps({
                            "migracao_id": migracao_id,
                            "instalacoes": len(plano["instalacoes"]),
                        }, ensure_ascii=False, sort_keys=True),
                        "quando": quando.isoformat(),
                    })
                resultado = self._validar_resultado_migracao(
                    total_antes, revogadas_antes, municipios_antes, len(planos),
                )
                self._migracoes_dados[migracao_id] = {
                    "migracao_id": migracao_id, "fingerprint": fingerprint,
                    "backup_referencia": backup_referencia, "aplicada_em": quando.isoformat(),
                    "resultado": copy.deepcopy(resultado),
                }
                return resultado
            except Exception:
                self._inst, self._licencas, self._eventos, self._migracoes_dados = snapshot
                raise

    def _validar_resultado_migracao(self, total_antes: int, revogadas_antes: int,
                                    municipios_antes: set[str],
                                    licencas_migradas: int) -> dict[str, Any]:
        total_depois = len(self._inst)
        revogadas_depois = sum(1 for i in self._inst.values() if i.get("revogada"))
        municipios_depois = {str(i.get("codigo_ibge") or "") for i in self._inst.values()}
        if total_depois != total_antes:
            raise ValueError("a quantidade total de instalações mudou durante a migração")
        if revogadas_depois != revogadas_antes:
            raise ValueError("a quantidade de revogações legadas mudou durante a migração")
        if municipios_depois != municipios_antes:
            raise ValueError("a lista de municípios mudou durante a migração")
        if any(not i.get("licenca_id") for i in self._inst.values()):
            raise ValueError("restaram instalações sem licença")
        if any(i.get("status_instalacao") == "ativa" and not i.get("licenca_id")
               for i in self._inst.values()):
            raise ValueError("existe instalação ativa sem licença")
        for licenca_id, licenca in self._licencas.items():
            if licenca.get("status") == "ativa" and (
                not licenca.get("expira_em") or not licenca.get("inicio_em")
                or not licenca.get("max_auditores")
            ):
                raise ValueError(f"licença ativa incompleta: {licenca_id}")
            if self.contar_instalacoes_ativas(licenca_id) > int(
                licenca.get("max_instalacoes_ativas") or 1
            ):
                raise ValueError(f"licença acima do limite de instalações: {licenca_id}")
        return {
            "total_instalacoes_antes": total_antes,
            "total_instalacoes_depois": total_depois,
            "municipios_antes": sorted(municipios_antes),
            "municipios_depois": sorted(municipios_depois),
            "revogadas_antes": revogadas_antes,
            "revogadas_depois": revogadas_depois,
            "licencas_migradas": licencas_migradas,
            "repetida": False,
        }

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
        self._engine = create_engine(url, pool_pre_ping=True, pool_recycle=300)
        self._criar_esquema()

    def _criar_esquema(self) -> None:
        from app.migracoes_schema import executar_migracoes
        executar_migracoes(self._engine)

    def obter_instalacao(self, instalacao_id: str) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(
                text("SELECT instalacao_id, codigo_ibge, max_usuarios, revogada, "
                     "licenca_id, status_instalacao, associada_em, ativada_em, "
                     "revogada_em, ultima_consulta_em "
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
                     "licenca_id, status_instalacao, associada_em, ativada_em, "
                     "revogada_em, ultima_consulta_em "
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
                licenca_id, codigo_ibge, nome_municipio, status, inicio_em, expira_em,
                max_auditores, max_instalacoes_ativas, dias_offline,
                versao_minima, criada_em, atualizada_em
            ) VALUES (
                :licenca_id, :codigo_ibge, :nome_municipio, :status, :inicio_em, :expira_em,
                :max_auditores, :max_instalacoes_ativas, :dias_offline,
                :versao_minima, :criada_em, :atualizada_em
            )"""), {**dados, "nome_municipio": str(dados.get("nome_municipio") or "")})
        return self.obter_licenca(str(dados["licenca_id"])) or dict(dados)

    def obter_licenca(self, licenca_id: str) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(text(
                "SELECT licenca_id, codigo_ibge, nome_municipio, status, inicio_em, expira_em, "
                "max_auditores, max_instalacoes_ativas, dias_offline, versao_minima, "
                "criada_em, atualizada_em, chave_migracao, migrada_em "
                "FROM licencas WHERE licenca_id = :l"
            ), {"l": licenca_id}).mappings().first()
        return self._datas_iso(dict(row)) if row else None

    def listar_licencas(self) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(text("""SELECT l.licenca_id, l.codigo_ibge, l.nome_municipio, l.status,
                l.inicio_em, l.expira_em, l.max_auditores, l.max_instalacoes_ativas,
                l.dias_offline, l.versao_minima, l.criada_em, l.atualizada_em,
                l.chave_migracao, l.migrada_em,
                COUNT(i.instalacao_id) FILTER (WHERE i.status_instalacao = 'ativa') AS instalacoes_ativas
                FROM licencas l LEFT JOIN instalacoes i ON i.licenca_id = l.licenca_id
                GROUP BY l.licenca_id ORDER BY l.criada_em""")).mappings().all()
        return [self._datas_iso(dict(r)) for r in rows]

    @staticmethod
    def _campos_recuperacao() -> str:
        return (
            "request_id, request_digest, installation_id, usuario_referencia, "
            "desafio_digest, versao_app, solicitado_em, solicitacao_expira_em, "
            "estado, operador_preparou, aprovador, emitido_por, protocolo, "
            "justificativa, metodo_verificacao, canal_oficial_confirmado, "
            "escalonamento_confirmado, kid, jti_digest, token_digest, emitido_em, "
            "token_expira_em, criado_em, atualizado_em"
        )

    def preparar_recuperacao(self, dados: dict[str, Any]) -> dict[str, Any]:
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        try:
            with self._engine.begin() as con:
                con.execute(text("""INSERT INTO recuperacoes_senha (
                    request_id, request_digest, installation_id, usuario_referencia,
                    desafio_digest, versao_app, solicitado_em, solicitacao_expira_em,
                    estado, operador_preparou, aprovador, emitido_por, protocolo,
                    justificativa, metodo_verificacao, canal_oficial_confirmado,
                    escalonamento_confirmado, kid, jti_digest, token_digest,
                    emitido_em, token_expira_em, criado_em, atualizado_em
                ) VALUES (
                    :request_id, :request_digest, :installation_id, :usuario_referencia,
                    :desafio_digest, :versao_app, :solicitado_em, :solicitacao_expira_em,
                    :estado, :operador_preparou, :aprovador, :emitido_por, :protocolo,
                    :justificativa, :metodo_verificacao, :canal_oficial_confirmado,
                    :escalonamento_confirmado, :kid, :jti_digest, :token_digest,
                    :emitido_em, :token_expira_em, :criado_em, :atualizado_em
                )"""), dados)
        except IntegrityError as exc:
            raise RecuperacaoDuplicada("Esta solicitação já foi registrada.") from exc
        return self.obter_recuperacao(str(dados["request_id"])) or dict(dados)

    def obter_recuperacao(self, request_id: str) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(text(
                f"SELECT {self._campos_recuperacao()} FROM recuperacoes_senha "
                "WHERE request_id=:r"
            ), {"r": request_id}).mappings().first()
        return self._datas_iso(dict(row)) if row else None

    def listar_recuperacoes(self, limite: int = 100,
                            estado: str = "") -> list[dict[str, Any]]:
        from sqlalchemy import text
        filtro = "WHERE estado=:e" if estado else ""
        parametros: dict[str, Any] = {"n": max(1, int(limite))}
        if estado:
            parametros["e"] = estado
        with self._engine.connect() as con:
            rows = con.execute(text(
                f"SELECT {self._campos_recuperacao()} FROM recuperacoes_senha "
                f"{filtro} ORDER BY atualizado_em DESC LIMIT :n"
            ), parametros).mappings().all()
        return [self._datas_iso(dict(row)) for row in rows]

    def aprovar_recuperacao(self, request_id: str, aprovador: str, quando: int,
                            dupla_aprovacao: bool = True) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        expirou = False
        with self._engine.begin() as con:
            row = con.execute(text(
                "SELECT estado, operador_preparou, solicitacao_expira_em "
                "FROM recuperacoes_senha WHERE request_id=:r FOR UPDATE"
            ), {"r": request_id}).mappings().first()
            if row is None:
                return None
            if row["estado"] != "preparada":
                raise RecuperacaoEstadoInvalido("A solicitação não está aguardando aprovação.")
            if int(row["solicitacao_expira_em"]) < int(quando):
                con.execute(text(
                    "UPDATE recuperacoes_senha SET estado='expirada', atualizado_em=:q "
                    "WHERE request_id=:r"
                ), {"q": int(quando), "r": request_id})
                expirou = True
            elif dupla_aprovacao and hmac_compare_texto(row["operador_preparou"], aprovador):
                raise RecuperacaoAutoaprovacao("Quem preparou não pode aprovar a solicitação.")
            elif not expirou:
                con.execute(text(
                    "UPDATE recuperacoes_senha SET estado='aprovada', aprovador=:a, "
                    "atualizado_em=:q WHERE request_id=:r"
                ), {"a": aprovador, "q": int(quando), "r": request_id})
        if expirou:
            raise RecuperacaoEstadoInvalido("A solicitação expirou.")
        return self.obter_recuperacao(request_id)

    def confirmar_emissao_recuperacao(
        self, request_id: str, request_digest: str, emitido_por: str,
        kid: str, jti_digest: str, token_digest: str,
        emitido_em: int, token_expira_em: int,
    ) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        expirou = False
        with self._engine.begin() as con:
            row = con.execute(text(
                "SELECT estado, request_digest, solicitacao_expira_em "
                "FROM recuperacoes_senha WHERE request_id=:r FOR UPDATE"
            ), {"r": request_id}).mappings().first()
            if row is None:
                return None
            if row["estado"] != "aprovada":
                raise RecuperacaoEstadoInvalido("A solicitação não está aprovada para emissão.")
            if not hmac_compare_texto(row["request_digest"], request_digest):
                raise RecuperacaoEstadoInvalido("A solicitação informada diverge da preparada.")
            if int(row["solicitacao_expira_em"]) < int(emitido_em):
                con.execute(text(
                    "UPDATE recuperacoes_senha SET estado='expirada', atualizado_em=:q "
                    "WHERE request_id=:r"
                ), {"q": int(emitido_em), "r": request_id})
                expirou = True
            else:
                con.execute(text("""UPDATE recuperacoes_senha SET
                    estado='emitida', emitido_por=:p, kid=:k, jti_digest=:j,
                    token_digest=:t, emitido_em=:q, token_expira_em=:x,
                    atualizado_em=:q WHERE request_id=:r"""), {
                    "p": emitido_por,
                    "k": kid,
                    "j": jti_digest,
                    "t": token_digest,
                    "q": int(emitido_em),
                    "x": int(token_expira_em),
                    "r": request_id,
                })
        if expirou:
            raise RecuperacaoEstadoInvalido("A solicitação expirou.")
        return self.obter_recuperacao(request_id)

    def contar_recuperacoes_recentes(self, operador: str, installation_id: str,
                                     desde: int) -> tuple[int, int]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            por_operador = con.execute(text(
                "SELECT count(*) FROM recuperacoes_senha "
                "WHERE operador_preparou=:o AND criado_em>=:d"
            ), {"o": operador, "d": int(desde)}).scalar_one()
            por_instalacao = con.execute(text(
                "SELECT count(*) FROM recuperacoes_senha "
                "WHERE installation_id=:i AND criado_em>=:d"
            ), {"i": installation_id, "d": int(desde)}).scalar_one()
        return int(por_operador), int(por_instalacao)

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
                nome_municipio=:nome_municipio, status=:status,
                inicio_em=:inicio_em, expira_em=:expira_em,
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
            # Serializa alterações do mesmo identificador mesmo quando duas
            # transferências concorrentes apontam para licenças diferentes.
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:i))"), {"i": instalacao_id})
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:l))"), {"l": licenca_id})
            licenca = con.execute(text(
                "SELECT codigo_ibge, max_instalacoes_ativas FROM licencas WHERE licenca_id=:l"
            ), {"l": licenca_id}).mappings().first()
            if licenca is None:
                raise ValueError("licença não encontrada")
            if status == "ativa":
                ativas = con.execute(text(
                    "SELECT count(*) FROM instalacoes WHERE licenca_id=:l "
                    "AND status_instalacao='ativa' AND instalacao_id<>:i"
                ), {"l": licenca_id, "i": instalacao_id}).scalar_one()
                if int(ativas) >= int(licenca["max_instalacoes_ativas"]):
                    raise ValueError("limite de instalações ativas da licença atingido")
            con.execute(text("""INSERT INTO instalacoes (
                instalacao_id, codigo_ibge, max_usuarios, revogada, licenca_id,
                status_instalacao, associada_em, ativada_em, revogada_em
            ) VALUES (:i, :c, NULL, :r, :l, :s, :q, :a, :v)
            ON CONFLICT (instalacao_id) DO UPDATE SET
                codigo_ibge=EXCLUDED.codigo_ibge, max_usuarios=NULL,
                revogada=EXCLUDED.revogada, licenca_id=EXCLUDED.licenca_id,
                status_instalacao=EXCLUDED.status_instalacao,
                associada_em=EXCLUDED.associada_em,
                ativada_em=EXCLUDED.ativada_em, revogada_em=EXCLUDED.revogada_em"""), {
                "i": instalacao_id, "c": licenca["codigo_ibge"], "l": licenca_id,
                "s": status, "r": status in {"revogada", "substituida"},
                "q": quando,
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

    def registrar_auditoria_admin(self, operador: str, acao: str, alvo: str,
                                  resultado: str, detalhes: str,
                                  quando: datetime) -> None:
        from sqlalchemy import text
        with self._engine.begin() as con:
            con.execute(text("""INSERT INTO auditoria_admin
                (operador, acao, alvo, resultado, detalhes, quando)
                VALUES (:o, :a, :al, :r, :d, :q)"""), {
                "o": operador, "a": acao, "al": alvo,
                "r": resultado, "d": detalhes, "q": quando,
            })

    def listar_auditoria_admin(self, limite: int = 100) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            rows = con.execute(text("""SELECT operador, acao, alvo, resultado, detalhes, quando
                FROM auditoria_admin ORDER BY quando DESC LIMIT :n"""), {
                "n": max(1, int(limite)),
            }).mappings().all()
        return [self._datas_iso(dict(r)) for r in rows]

    def aplicar_retencao(self, agora: datetime, tentativas_dias: int,
                         auditoria_dias: int) -> dict[str, int]:
        from sqlalchemy import text
        corte_tentativas = agora - timedelta(days=tentativas_dias)
        corte_auditoria = agora - timedelta(days=auditoria_dias)
        with self._engine.begin() as con:
            tentativas = con.execute(
                text("DELETE FROM tentativas WHERE quando < :c"), {"c": corte_tentativas}
            ).rowcount or 0
            eventos = con.execute(
                text("DELETE FROM eventos_licenca WHERE quando < :c"), {"c": corte_auditoria}
            ).rowcount or 0
            auditoria = con.execute(
                text("DELETE FROM auditoria_admin WHERE quando < :c"), {"c": corte_auditoria}
            ).rowcount or 0
            recuperacoes = con.execute(
                text("DELETE FROM recuperacoes_senha WHERE atualizado_em < :c"),
                {"c": int(corte_auditoria.timestamp())},
            ).rowcount or 0
        return {
            "tentativas": int(tentativas),
            "eventos_licenca": int(eventos),
            "auditoria_admin": int(auditoria),
            "recuperacoes_senha": int(recuperacoes),
        }

    def obter_migracao_dados(self, migracao_id: str) -> Optional[dict[str, Any]]:
        from sqlalchemy import text
        with self._engine.connect() as con:
            row = con.execute(text(
                "SELECT migracao_id, fingerprint, backup_referencia, aplicada_em, resultado "
                "FROM migracoes_dados WHERE migracao_id=:m"
            ), {"m": migracao_id}).mappings().first()
        if not row:
            return None
        reg = self._datas_iso(dict(row))
        try:
            reg["resultado"] = json.loads(reg["resultado"])
        except (TypeError, ValueError):
            reg["resultado"] = {}
        return reg

    def migracao_legado_concluida(self) -> bool:
        from sqlalchemy import text
        with self._engine.connect() as con:
            return con.execute(text(
                "SELECT 1 FROM migracoes_dados WHERE migracao_id LIKE 'legado-v1-%' LIMIT 1"
            )).first() is not None

    def aplicar_migracao_legada(self, migracao_id: str, fingerprint: str,
                                backup_referencia: str, planos: list[dict[str, Any]],
                                quando: datetime) -> dict[str, Any]:
        from sqlalchemy import text

        with self._engine.begin() as con:
            con.execute(text(
                "SELECT pg_advisory_xact_lock(hashtext('techfisco-migracao-legada-v1'))"
            ))
            # Congela as fontes da migração. Escritas administrativas concorrentes
            # aguardam o commit ou rollback, em vez de entrar entre a conferência
            # do fingerprint e as contagens finais.
            con.execute(text("LOCK TABLE instalacoes IN SHARE ROW EXCLUSIVE MODE"))
            con.execute(text("LOCK TABLE licencas IN SHARE ROW EXCLUSIVE MODE"))
            con.execute(text("LOCK TABLE municipios_revogados IN SHARE ROW EXCLUSIVE MODE"))
            anterior = con.execute(text(
                "SELECT resultado FROM migracoes_dados WHERE migracao_id=:m"
            ), {"m": migracao_id}).scalar_one_or_none()
            if anterior is not None:
                resultado = json.loads(str(anterior))
                return {**resultado, "repetida": True}

            total_antes = int(con.execute(text("SELECT count(*) FROM instalacoes")).scalar_one())
            revogadas_antes = int(con.execute(text(
                "SELECT count(*) FROM instalacoes WHERE revogada=TRUE"
            )).scalar_one())
            municipios_antes = {
                str(r[0]) for r in con.execute(text(
                    "SELECT DISTINCT codigo_ibge FROM instalacoes ORDER BY codigo_ibge"
                )).all()
            }

            for plano in planos:
                licenca = plano["licenca"]
                licenca_id = str(licenca["licenca_id"])
                municipio_revogado = con.execute(text(
                    "SELECT 1 FROM municipios_revogados WHERE codigo_ibge=:c"
                ), {"c": licenca["codigo_ibge"]}).first() is not None
                if municipio_revogado != bool(plano.get("municipio_revogado_esperado")):
                    raise ValueError(
                        f"revogação municipal mudou durante a migração: {licenca['codigo_ibge']}"
                    )
                existente = con.execute(text(
                    "SELECT licenca_id, codigo_ibge, chave_migracao, status, "
                    "inicio_em, expira_em, max_auditores, max_instalacoes_ativas, "
                    "dias_offline, versao_minima FROM licencas "
                    "WHERE licenca_id=:l FOR UPDATE"
                ), {"l": licenca_id}).mappings().first()
                if existente:
                    if str(existente["codigo_ibge"]) != str(licenca["codigo_ibge"]):
                        raise ValueError(f"licença existente pertence a outro município: {licenca_id}")
                    if existente["chave_migracao"] not in (None, licenca["chave_migracao"]):
                        raise ValueError(f"colisão de licença migrada: {licenca_id}")
                    for campo in (
                        "status", "max_auditores", "max_instalacoes_ativas",
                        "dias_offline", "versao_minima",
                    ):
                        if existente[campo] != licenca[campo]:
                            raise ValueError(
                                f"licença mudou durante a migração: {licenca_id} ({campo})"
                            )
                    for campo in ("inicio_em", "expira_em"):
                        atual = existente[campo]
                        atual_iso = atual.isoformat() if atual is not None else None
                        esperado = licenca[campo]
                        if atual_iso != esperado:
                            raise ValueError(
                                f"licença mudou durante a migração: {licenca_id} ({campo})"
                            )
                    con.execute(text(
                        "UPDATE licencas SET chave_migracao=:c, migrada_em=:q "
                        "WHERE licenca_id=:l"
                    ), {"c": licenca["chave_migracao"], "q": quando, "l": licenca_id})
                else:
                    con.execute(text("""INSERT INTO licencas (
                        licenca_id, codigo_ibge, status, inicio_em, expira_em,
                        max_auditores, max_instalacoes_ativas, dias_offline,
                        versao_minima, criada_em, atualizada_em, chave_migracao, migrada_em
                    ) VALUES (
                        :licenca_id, :codigo_ibge, :status, :inicio_em, :expira_em,
                        :max_auditores, :max_instalacoes_ativas, :dias_offline,
                        :versao_minima, :criada_em, :atualizada_em, :chave_migracao, :migrada_em
                    )"""), licenca)

                for vinculo in plano["instalacoes"]:
                    iid = str(vinculo["instalacao_id"])
                    instalacao = con.execute(text(
                        "SELECT codigo_ibge, licenca_id, revogada, max_usuarios FROM instalacoes "
                        "WHERE instalacao_id=:i FOR UPDATE"
                    ), {"i": iid}).mappings().first()
                    if instalacao is None:
                        raise ValueError(f"instalação desapareceu durante a migração: {iid}")
                    if str(instalacao["codigo_ibge"] or "") != str(licenca["codigo_ibge"]):
                        raise ValueError(f"município da instalação mudou durante a migração: {iid}")
                    if (
                        str(instalacao["codigo_ibge"] or "") != vinculo["codigo_ibge_esperado"]
                        or bool(instalacao["revogada"]) != vinculo["revogada_esperada"]
                        or instalacao["max_usuarios"] != vinculo["max_usuarios_esperado"]
                    ):
                        raise ValueError(f"instalação mudou durante a migração: {iid}")
                    if instalacao["licenca_id"] not in (None, licenca_id):
                        raise ValueError(f"instalação já pertence a outra licença: {iid}")
                    status = str(vinculo["status"])
                    con.execute(text("""UPDATE instalacoes SET
                        licenca_id=:l, status_instalacao=:s,
                        associada_em=:q,
                        ativada_em=CASE WHEN :s='ativa' THEN :q ELSE NULL END,
                        revogada_em=CASE WHEN :s IN ('revogada','substituida') THEN :q ELSE NULL END
                        WHERE instalacao_id=:i"""), {
                        "l": licenca_id, "s": status, "q": quando, "i": iid,
                    })
                con.execute(text(
                    "INSERT INTO eventos_licenca (licenca_id, acao, detalhes, quando) "
                    "VALUES (:l, 'migracao_legada', :d, :q)"
                ), {
                    "l": licenca_id,
                    "d": json.dumps({
                        "migracao_id": migracao_id,
                        "instalacoes": len(plano["instalacoes"]),
                    }, ensure_ascii=False, sort_keys=True),
                    "q": quando,
                })

            total_depois = int(con.execute(text("SELECT count(*) FROM instalacoes")).scalar_one())
            revogadas_depois = int(con.execute(text(
                "SELECT count(*) FROM instalacoes WHERE revogada=TRUE"
            )).scalar_one())
            municipios_depois = {
                str(r[0]) for r in con.execute(text(
                    "SELECT DISTINCT codigo_ibge FROM instalacoes ORDER BY codigo_ibge"
                )).all()
            }
            sem_licenca = int(con.execute(text(
                "SELECT count(*) FROM instalacoes WHERE licenca_id IS NULL"
            )).scalar_one())
            ativas_incompletas = int(con.execute(text("""SELECT count(*) FROM licencas
                WHERE status='ativa' AND (
                    inicio_em IS NULL OR expira_em IS NULL OR max_auditores IS NULL
                )""")).scalar_one())
            excesso = int(con.execute(text("""SELECT count(*) FROM (
                SELECT l.licenca_id FROM licencas l
                LEFT JOIN instalacoes i ON i.licenca_id=l.licenca_id
                  AND i.status_instalacao='ativa'
                GROUP BY l.licenca_id, l.max_instalacoes_ativas
                HAVING count(i.instalacao_id) > l.max_instalacoes_ativas
            ) x""")).scalar_one())
            if total_antes != total_depois:
                raise ValueError("a quantidade total de instalações mudou durante a migração")
            if revogadas_antes != revogadas_depois:
                raise ValueError("a quantidade de revogações legadas mudou durante a migração")
            if municipios_antes != municipios_depois:
                raise ValueError("a lista de municípios mudou durante a migração")
            if sem_licenca:
                raise ValueError(f"restaram {sem_licenca} instalações sem licença")
            if ativas_incompletas:
                raise ValueError(f"existem {ativas_incompletas} licenças ativas incompletas")
            if excesso:
                raise ValueError(f"existem {excesso} licenças acima do limite de instalações")

            resultado = {
                "total_instalacoes_antes": total_antes,
                "total_instalacoes_depois": total_depois,
                "municipios_antes": sorted(municipios_antes),
                "municipios_depois": sorted(municipios_depois),
                "revogadas_antes": revogadas_antes,
                "revogadas_depois": revogadas_depois,
                "licencas_migradas": len(planos),
                "repetida": False,
            }
            con.execute(text("""INSERT INTO migracoes_dados (
                migracao_id, fingerprint, backup_referencia, aplicada_em, resultado
            ) VALUES (:m, :f, :b, :q, :r)"""), {
                "m": migracao_id, "f": fingerprint, "b": backup_referencia,
                "q": quando, "r": json.dumps(resultado, ensure_ascii=False, sort_keys=True),
            })
            return resultado

    def pronto(self) -> bool:
        from sqlalchemy import text
        from app.migracoes_schema import schema_atual
        try:
            with self._engine.connect() as con:
                con.execute(text("SELECT 1"))
            return schema_atual(self._engine)
        except Exception:
            return False


def repositorio_do_ambiente(database_url: Optional[str]) -> Repositorio:
    if database_url and database_url.strip():
        return RepositorioPostgres(database_url.strip())
    return RepositorioMemoria()
