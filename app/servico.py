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

from app import contrato
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

    resp["max_usuarios"] = autorizacao.get("max_usuarios")
    if autorizacao.get("revogada") or repo.municipio_revogado(ibge):
        resp["status"] = "revogada"
        return resp

    resp["status"] = "ativa"
    resp["expira_em"] = _iso(agora + timedelta(days=cfg.dias_validade))
    resp["offline_ate"] = _iso(agora + timedelta(days=cfg.dias_offline))
    return resp
