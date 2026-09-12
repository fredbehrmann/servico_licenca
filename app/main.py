# -*- coding: utf-8 -*-
"""API do serviço de licença (FastAPI).

Endpoints:
- ``POST /v1/consulta`` — recebe os quatro campos, decide o status, assina e
  devolve a licença. Único endpoint que o app do auditor chama.
- ``POST /admin/autorizar`` / ``/admin/revogar`` / ``/admin/revogar-municipio`` /
  ``GET /admin/instalacoes`` — administração da allowlist, protegidos por
  ``ADMIN_TOKEN`` (Bearer).
- ``GET /health`` / ``GET /ready`` — vida e prontidão (banco), para o deploy.

Configuração por variáveis de ambiente (ver README):
  LICENCA_PRIVADA_PEM, LICENCA_KEY_ID, LICENCA_AMBIENTE, LICENCA_VERSAO_MINIMA,
  LICENCA_DIAS_VALIDADE, LICENCA_DIAS_OFFLINE, ADMIN_TOKEN, DATABASE_URL,
  LICENCA_RATE_MAX, LICENCA_RATE_JANELA_S.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import contrato
from app.painel import PAGINA_ADMIN
from app.assinador import Assinador, assinador_do_ambiente
from app.limite import LimitadorMemoria
from app.repositorio import Repositorio, repositorio_do_ambiente
from app.servico import Config, decidir

_log = logging.getLogger("licenca.servico")


# ─── Estado do processo (montado no startup) ─────────────────────────────────


class Estado:
    repo: Repositorio
    assinador: Optional[Assinador]
    cfg: Config
    limitador: LimitadorMemoria
    admin_token: str


estado = Estado()


def _int_env(nome: str, padrao: int) -> int:
    try:
        return int(os.environ.get(nome, "").strip() or padrao)
    except ValueError:
        return padrao


def montar_estado() -> None:
    estado.cfg = Config(
        key_id=os.environ.get("LICENCA_KEY_ID", "").strip() or "prod-ed25519-v1",
        ambiente=os.environ.get("LICENCA_AMBIENTE", "").strip() or contrato.AMBIENTE_PRODUCAO,
        versao_minima=os.environ.get("LICENCA_VERSAO_MINIMA", "").strip() or "1.0.0",
        dias_validade=_int_env("LICENCA_DIAS_VALIDADE", 365),
        dias_offline=_int_env("LICENCA_DIAS_OFFLINE", 7),
    )
    estado.repo = repositorio_do_ambiente(os.environ.get("DATABASE_URL"))
    estado.limitador = LimitadorMemoria(
        maximo=_int_env("LICENCA_RATE_MAX", 12),
        janela_s=_int_env("LICENCA_RATE_JANELA_S", 3600),
    )
    estado.admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
    try:
        estado.assinador = assinador_do_ambiente(
            os.environ.get("LICENCA_PRIVADA_PEM", ""), estado.cfg.key_id
        )
    except Exception as exc:
        # Sobe sem assinador: /health responde, mas /v1/consulta recusa com 503.
        # Falhar barulhento no consulta é melhor que assinar com chave errada.
        _log.error("assinador indisponível no startup: %s", exc)
        estado.assinador = None


app = FastAPI(title="TechFisco — Serviço de Licença", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    montar_estado()


# ─── Consulta (o app do auditor) ─────────────────────────────────────────────


@app.post("/v1/consulta")
async def consulta(request: Request) -> JSONResponse:
    try:
        corpo = await request.json()
        if not isinstance(corpo, dict):
            raise ValueError("corpo não é objeto JSON")
        req = contrato.extrair_requisicao(corpo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    if not estado.limitador.permitido(req["instalacao_id"]):
        raise HTTPException(status_code=429, detail="muitas consultas; tente mais tarde")

    if estado.assinador is None:
        raise HTTPException(status_code=503, detail="serviço sem chave de assinatura")

    agora = datetime.now(timezone.utc)
    resposta = decidir(req, estado.repo, estado.cfg, agora)
    resposta["assinatura"] = estado.assinador.assinar(resposta)

    # Registro de tentativa (sem dado pessoal) e log resumido — nunca a assinatura.
    try:
        estado.repo.registrar_tentativa(
            req["instalacao_id"], req["codigo_ibge"], resposta["status"], agora
        )
    except Exception:
        _log.exception("falha ao registrar tentativa")
    _log.info("consulta instalacao=%s ibge=%s status=%s",
              req["instalacao_id"], req["codigo_ibge"], resposta["status"])
    return JSONResponse(resposta)


# ─── Administração (allowlist) ───────────────────────────────────────────────


def exigir_admin(authorization: str = Header(default="")) -> None:
    esperado = estado.admin_token
    if not esperado:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN não configurado")
    prefixo = "Bearer "
    recebido = authorization[len(prefixo):] if authorization.startswith(prefixo) else ""
    if not hmac.compare_digest(recebido, esperado):
        raise HTTPException(status_code=401, detail="token administrativo inválido")


@app.post("/admin/autorizar", dependencies=[Depends(exigir_admin)])
async def autorizar(request: Request) -> dict[str, Any]:
    corpo = await request.json()
    inst = str(corpo.get("instalacao_id") or "").strip()
    ibge = str(corpo.get("codigo_ibge") or "").strip()
    if not inst or not ibge:
        raise HTTPException(status_code=400, detail="instalacao_id e codigo_ibge obrigatórios")
    maxu = corpo.get("max_usuarios")
    maxu = int(maxu) if maxu not in (None, "") else None
    estado.repo.autorizar(inst, ibge, maxu)
    _log.info("autorizada instalacao=%s ibge=%s max=%s", inst, ibge, maxu)
    return {"ok": True, "instalacao_id": inst}


@app.post("/admin/revogar", dependencies=[Depends(exigir_admin)])
async def revogar(request: Request) -> dict[str, Any]:
    corpo = await request.json()
    inst = str(corpo.get("instalacao_id") or "").strip()
    if not inst:
        raise HTTPException(status_code=400, detail="instalacao_id obrigatório")
    achou = estado.repo.revogar_instalacao(inst)
    _log.info("revogada instalacao=%s achou=%s", inst, achou)
    return {"ok": True, "revogada": achou}


@app.post("/admin/revogar-municipio", dependencies=[Depends(exigir_admin)])
async def revogar_municipio(request: Request) -> dict[str, Any]:
    corpo = await request.json()
    ibge = str(corpo.get("codigo_ibge") or "").strip()
    if not ibge:
        raise HTTPException(status_code=400, detail="codigo_ibge obrigatório")
    estado.repo.revogar_municipio(ibge)
    _log.info("revogado municipio ibge=%s", ibge)
    return {"ok": True, "codigo_ibge": ibge}


@app.post("/admin/reativar-municipio", dependencies=[Depends(exigir_admin)])
async def reativar_municipio(request: Request) -> dict[str, Any]:
    corpo = await request.json()
    ibge = str(corpo.get("codigo_ibge") or "").strip()
    if not ibge:
        raise HTTPException(status_code=400, detail="codigo_ibge obrigatório")
    estado.repo.reativar_municipio(ibge)
    _log.info("reativado municipio ibge=%s", ibge)
    return {"ok": True, "codigo_ibge": ibge}


@app.get("/admin/instalacoes", dependencies=[Depends(exigir_admin)])
async def listar() -> dict[str, Any]:
    return {"ok": True, "instalacoes": estado.repo.listar_instalacoes()}


@app.get("/admin/municipios-revogados", dependencies=[Depends(exigir_admin)])
async def listar_municipios_revogados() -> dict[str, Any]:
    return {"ok": True, "codigos": estado.repo.listar_municipios_revogados()}


@app.get("/admin", response_class=HTMLResponse)
async def painel_admin() -> HTMLResponse:
    """Painel web de administração. A página é pública (só o formulário); as
    ações exigem o ADMIN_TOKEN, enviado do navegador aos endpoints /admin/*."""
    return HTMLResponse(PAGINA_ADMIN)


# ─── Saúde ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "ambiente": estado.cfg.ambiente, "key_id": estado.cfg.key_id}


@app.get("/ready")
async def ready() -> JSONResponse:
    pronto = estado.assinador is not None and estado.repo.pronto()
    return JSONResponse({"ok": pronto}, status_code=200 if pronto else 503)
