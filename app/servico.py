# -*- coding: utf-8 -*-
"""Decisão pura do status da licença — sem rede, sem assinatura, sem IO de HTTP.

Recebe a requisição já validada (quatro campos), o repositório e a configuração;
devolve a resposta **sem assinatura**. Quem assina é o main. Manter isto puro
deixa a regra de negócio testável sem servidor nem banco real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app import contrato, licencas
from app.repositorio import Repositorio


@dataclass(frozen=True)
class Config:
    key_id: str = "prod-ed25519-v1"
    ambiente: str = contrato.AMBIENTE_PRODUCAO
    versao_minima: str = "1.0.0"
    dias_validade: int = 365
    dias_offline: int = 7               # padrão do serviço; a licença carimba o prazo


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def decidir(
    req: dict[str, str], repo: Repositorio, cfg: Config, agora: Optional[datetime] = None
) -> dict[str, Any]:
    """Monta a resposta (sem assinatura) para uma requisição autenticada.

    - ``nonce`` volta igual ao da requisição (barra reapresentação).
    - vínculo: ``codigo_ibge``/``instalacao_id`` ecoam os da requisição, mesmo numa
      negação — o app confere o vínculo e só confia num "não" que também venha
      assinado e casando.
    """
    agora = agora or datetime.now(timezone.utc)
    inst = req["instalacao_id"]
    ibge = req["codigo_ibge"]

    resp: dict[str, Any] = {
        "codigo_ibge": ibge,
        "instalacao_id": inst,
        "nonce": req["nonce"],
        "emitida_em": _iso(agora),
        "versao_minima": cfg.versao_minima,
        "key_id": cfg.key_id,
        "ambiente": cfg.ambiente,
        "expira_em": None,
        "offline_ate": None,
        "max_usuarios": None,
    }

    autorizacao = repo.obter_instalacao(inst)
    if autorizacao is None:
        resp["status"] = "instalacao_nao_autorizada"
        return resp

    # A autorização pertence ao PAR instalação + município. Localizar apenas
    # pelo instalacao_id não basta: sem esta comparação, uma instalação
    # autorizada para um município poderia consultar como se fosse de outro.
    # A negação é propositalmente genérica e não devolve o IBGE cadastrado,
    # para não transformar o endpoint em mecanismo de descoberta da allowlist.
    if str(autorizacao.get("codigo_ibge") or "") != ibge:
        resp["status"] = "instalacao_nao_autorizada"
        return resp

    # Modelo contratual novo. Registros antigos não têm licenca_id e seguem no
    # bloco de compatibilidade abaixo até a migração explícita da Etapa 4.
    licenca_id = str(autorizacao.get("licenca_id") or "")
    if licenca_id:
        status_inst = str(autorizacao.get("status_instalacao") or "")
        if status_inst in {"revogada", "substituida"}:
            resp["status"] = "revogada"
            return resp
        if status_inst != "ativa":               # contingência não autoriza uso simultâneo
            resp["status"] = "instalacao_nao_autorizada"
            return resp

        licenca = repo.obter_licenca(licenca_id)
        if licenca is None or str(licenca.get("codigo_ibge") or "") != ibge:
            resp["status"] = "instalacao_nao_autorizada"
            return resp
        if repo.municipio_revogado(ibge):
            resp["status"] = "revogada"
            return resp

        status_licenca = str(licenca.get("status") or "pendente")
        if status_licenca in {"suspensa", "revogada"}:
            resp["status"] = "revogada"
            return resp
        if status_licenca == "pendente":
            resp["status"] = "instalacao_nao_autorizada"
            return resp

        try:
            inicio = licencas.parse_data(licenca.get("inicio_em"), "inicio_em")
            fim = licencas.parse_data(licenca.get("expira_em"), "expira_em")
        except licencas.LicencaInvalida:
            resp["status"] = "instalacao_nao_autorizada"  # falha fechada
            return resp
        if agora < inicio:
            resp["status"] = "instalacao_nao_autorizada"
            return resp
        if status_licenca == "expirada" or agora >= fim:
            resp["status"] = "expirada"
            resp["expira_em"] = _iso(fim)
            return resp

        max_inst = int(licenca.get("max_instalacoes_ativas") or 1)
        if repo.contar_instalacoes_ativas(licenca_id) > max_inst:
            # Inconsistência administrativa: falha fechada até o cadastro ser reparado.
            resp["status"] = "instalacao_nao_autorizada"
            return resp

        resp["status"] = "ativa"
        resp["max_usuarios"] = int(licenca["max_auditores"])
        resp["versao_minima"] = str(licenca.get("versao_minima") or cfg.versao_minima)
        resp["expira_em"] = _iso(fim)
        dias_offline = int(licenca.get("dias_offline") or cfg.dias_offline)
        resp["offline_ate"] = _iso(min(agora + timedelta(days=dias_offline), fim))
        return resp

    # Compatibilidade temporária: allowlist anterior ao modelo contratual. A
    # Etapa 4 converterá estes registros sem inventar data de contrato.
    resp["max_usuarios"] = autorizacao.get("max_usuarios")
    if autorizacao.get("revogada") or repo.municipio_revogado(ibge):
        resp["status"] = "revogada"
        return resp

    resp["status"] = "ativa"
    resp["expira_em"] = _iso(agora + timedelta(days=cfg.dias_validade))
    resp["offline_ate"] = _iso(agora + timedelta(days=cfg.dias_offline))
    return resp
