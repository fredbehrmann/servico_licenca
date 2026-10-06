# -*- coding: utf-8 -*-
"""API do serviço de licença (FastAPI).

Endpoints:
- ``POST /v1/consulta`` — recebe os quatro campos, decide o status, assina e
  devolve a licença. Único endpoint que o app do auditor chama.
- ``/admin/licencas`` — contratos, instalações vinculadas e histórico;
  os endpoints antigos da allowlist permanecem compatíveis até a Etapa 4.
- ``GET /health`` / ``GET /ready`` — vida e prontidão (banco), para o deploy.

Configuração por variáveis de ambiente (ver README):
  LICENCA_PRIVADA_PEM, LICENCA_KEY_ID, LICENCA_AMBIENTE, LICENCA_VERSAO_MINIMA,
  LICENCA_DIAS_VALIDADE, LICENCA_DIAS_OFFLINE, ADMIN_TOKEN, DATABASE_URL,
  LICENCA_RATE_MAX, LICENCA_RATE_JANELA_S, LICENCA_REPLICAS,
  LICENCA_RETENCAO_TENTATIVAS_DIAS, LICENCA_RETENCAO_AUDITORIA_DIAS.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app import admin_auth, contrato, licencas, recuperacao_senha, validacao
from app.painel import PAGINA_ADMIN
from app.assinador import Assinador, assinador_do_ambiente
from app.assinador_recuperacao import (
    AssinadorRecuperacao,
    assinador_recuperacao_do_ambiente,
)
from app.configuracao import (
    ConfiguracaoOperacional,
    DiagnosticoProntidao,
    diagnosticar,
)
from app.limite import LimitadorMemoria
from app.repositorio import (
    RecuperacaoAutoaprovacao,
    RecuperacaoDuplicada,
    RecuperacaoEstadoInvalido,
    Repositorio,
    RepositorioMemoria,
    repositorio_do_ambiente,
)
from app.servico import Config, decidir

_log = logging.getLogger("licenca.servico")


# ─── Estado do processo (montado no startup) ─────────────────────────────────


class Estado:
    repo: Repositorio
    assinador: Optional[Assinador]
    cfg: Config
    limitador: LimitadorMemoria
    admin_token: str
    operacional: ConfiguracaoOperacional
    diagnostico: DiagnosticoProntidao
    assinador_recuperacao: Optional[AssinadorRecuperacao]
    oidc: admin_auth.ConfiguracaoOidc
    recuperacao_habilitada: bool
    recuperacao_token_ttl_s: int
    recuperacao_rate_max: int
    recuperacao_rate_janela_s: int
    recuperacao_dupla_aprovacao: bool


estado = Estado()


def _int_env(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        return padrao
    try:
        return int(valor)
    except ValueError:
        return 0


def _bool_env(nome: str, padrao: bool = False) -> bool:
    valor = os.environ.get(nome, "").strip().lower()
    if not valor:
        return padrao
    return valor in {"1", "true", "sim", "yes", "on"}


def _lista_env(nome: str) -> tuple[str, ...]:
    return tuple(
        item.strip() for item in os.environ.get(nome, "").split(",") if item.strip()
    )


def _atualizar_prontidao() -> DiagnosticoProntidao:
    estado.diagnostico = diagnosticar(
        estado.operacional,
        estado.repo,
        estado.assinador,
        estado.assinador_recuperacao,
    )
    return estado.diagnostico


def montar_estado() -> None:
    ambiente_bruto = os.environ.get("LICENCA_AMBIENTE", "").strip()
    key_id = os.environ.get("LICENCA_KEY_ID", "").strip()
    versao_minima = os.environ.get("LICENCA_VERSAO_MINIMA", "").strip() or "1.0.0"
    dias_validade = _int_env("LICENCA_DIAS_VALIDADE", 365)
    dias_offline = _int_env("LICENCA_DIAS_OFFLINE", 7)
    rate_max = _int_env("LICENCA_RATE_MAX", 12)
    rate_janela_s = _int_env("LICENCA_RATE_JANELA_S", 3600)
    replicas_bruto = os.environ.get("LICENCA_REPLICAS", "").strip()
    replicas = _int_env("LICENCA_REPLICAS", 1)
    retencao_tentativas = _int_env("LICENCA_RETENCAO_TENTATIVAS_DIAS", 90)
    retencao_auditoria = _int_env("LICENCA_RETENCAO_AUDITORIA_DIAS", 730)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    publica_esperada_b64 = os.environ.get("LICENCA_PUBLICA_B64_ESPERADA", "").strip()
    estado.admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
    estado.cfg = Config(
        key_id=key_id,
        ambiente=ambiente_bruto or "nao_configurado",
        versao_minima=versao_minima,
        dias_validade=dias_validade,
        dias_offline=dias_offline,
    )
    try:
        estado.repo = repositorio_do_ambiente(database_url)
    except Exception:
        # Mantém somente /health disponível. As demais rotas ficam fechadas pelo
        # diagnóstico; produção nunca passa a operar silenciosamente em memória.
        _log.exception("banco indisponível no startup")
        estado.repo = RepositorioMemoria()
    estado.limitador = LimitadorMemoria(
        maximo=rate_max,
        janela_s=rate_janela_s,
    )
    try:
        estado.assinador = assinador_do_ambiente(
            os.environ.get("LICENCA_PRIVADA_PEM", ""), estado.cfg.key_id
        )
    except Exception as exc:
        # Sobe sem assinador: /health responde, mas /v1/consulta recusa com 503.
        # Falhar barulhento no consulta é melhor que assinar com chave errada.
        _log.error("assinador indisponível no startup: %s", exc)
        estado.assinador = None
    estado.recuperacao_habilitada = _bool_env("RECUPERACAO_HABILITADA", False)
    recuperacao_key_id = os.environ.get("RECUPERACAO_KEY_ID", "").strip()
    recuperacao_publica = os.environ.get("RECUPERACAO_PUBLICA_B64_ESPERADA", "").strip()
    estado.recuperacao_token_ttl_s = _int_env("RECUPERACAO_TOKEN_TTL_SEGUNDOS", 900)
    estado.recuperacao_rate_max = _int_env("RECUPERACAO_RATE_MAX", 5)
    estado.recuperacao_rate_janela_s = _int_env("RECUPERACAO_RATE_JANELA_S", 3600)
    estado.recuperacao_dupla_aprovacao = _bool_env(
        "RECUPERACAO_DUPLA_APROVACAO", True,
    )
    estado.oidc = admin_auth.ConfiguracaoOidc(
        habilitado=_bool_env("OIDC_HABILITADO", False),
        issuer=os.environ.get("OIDC_ISSUER", "").strip(),
        authorization_endpoint=os.environ.get("OIDC_AUTHORIZATION_ENDPOINT", "").strip(),
        token_endpoint=os.environ.get("OIDC_TOKEN_ENDPOINT", "").strip(),
        jwks_uri=os.environ.get("OIDC_JWKS_URI", "").strip(),
        client_id=os.environ.get("OIDC_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("OIDC_CLIENT_SECRET", "").strip(),
        redirect_uri=os.environ.get("OIDC_REDIRECT_URI", "").strip(),
        session_secret=os.environ.get("OIDC_SESSION_SECRET", "").strip(),
        grupo_licencas=_lista_env("OIDC_GRUPO_LICENCAS_OPERADOR"),
        grupo_recuperacao_operador=_lista_env("OIDC_GRUPO_RECUPERACAO_OPERADOR"),
        grupo_recuperacao_aprovador=_lista_env("OIDC_GRUPO_RECUPERACAO_APROVADOR"),
        grupo_auditoria=_lista_env("OIDC_GRUPO_AUDITORIA_LEITURA"),
        exigir_mfa=_bool_env("OIDC_EXIGIR_MFA", True),
    )
    estado.assinador_recuperacao = None
    if estado.recuperacao_habilitada:
        try:
            estado.assinador_recuperacao = assinador_recuperacao_do_ambiente(
                os.environ.get("RECUPERACAO_PRIVADA_PEM", ""), recuperacao_key_id,
            )
        except Exception as exc:
            _log.error("assinador de recuperação indisponível no startup: %s", exc)
    estado.operacional = ConfiguracaoOperacional(
        ambiente=estado.cfg.ambiente,
        ambiente_explicito=bool(ambiente_bruto),
        database_url=database_url,
        key_id=key_id,
        publica_esperada_b64=publica_esperada_b64,
        admin_token=estado.admin_token,
        versao_minima=versao_minima,
        dias_offline=dias_offline,
        dias_validade=dias_validade,
        rate_max=rate_max,
        rate_janela_s=rate_janela_s,
        replicas=replicas,
        replicas_explicitas=bool(replicas_bruto),
        retencao_tentativas_dias=retencao_tentativas,
        retencao_auditoria_dias=retencao_auditoria,
        recuperacao_habilitada=estado.recuperacao_habilitada,
        recuperacao_key_id=recuperacao_key_id,
        recuperacao_publica_esperada_b64=recuperacao_publica,
        recuperacao_token_ttl_s=estado.recuperacao_token_ttl_s,
        recuperacao_rate_max=estado.recuperacao_rate_max,
        recuperacao_rate_janela_s=estado.recuperacao_rate_janela_s,
        recuperacao_dupla_aprovacao=estado.recuperacao_dupla_aprovacao,
        oidc_configurado=estado.oidc.configurado,
    )
    diagnostico = _atualizar_prontidao()
    if not diagnostico.pronto:
        _log.critical("serviço NÃO PRONTO: %s", ",".join(diagnostico.problemas))
        return
    try:
        removidos = estado.repo.aplicar_retencao(
            datetime.now(timezone.utc), retencao_tentativas, retencao_auditoria,
        )
        _log.info("retenção aplicada: %s", removidos)
    except Exception:
        _log.exception("falha ao aplicar retenção no startup")


app = FastAPI(title="TechFisco — Serviço de Licença", docs_url=None, redoc_url=None)


def _cookie_seguro() -> bool:
    return estado.cfg.ambiente == contrato.AMBIENTE_PRODUCAO


def _identidade_sessao(request: Request) -> admin_auth.IdentidadeAdmin | None:
    if not estado.oidc.session_secret:
        return None
    valor = request.cookies.get(admin_auth.COOKIE_SESSAO, "")
    if not valor:
        return None
    try:
        return admin_auth.ler_cookie_sessao(valor, estado.oidc.session_secret)
    except admin_auth.ErroAutenticacao:
        return None


def _operador_identidade(identidade: admin_auth.IdentidadeAdmin) -> str:
    return f"oidc:{identidade.subject}"[:240]


def _exigir_csrf(request: Request, identidade: admin_auth.IdentidadeAdmin) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    recebido = request.headers.get("X-CSRF-Token", "")
    if not recebido or not hmac.compare_digest(recebido, identidade.csrf):
        raise HTTPException(status_code=403, detail="proteção CSRF inválida")


def _exigir_identidade_recuperacao(
    request: Request, papeis: set[str],
) -> admin_auth.IdentidadeAdmin:
    if not estado.recuperacao_habilitada:
        raise HTTPException(status_code=503, detail="recuperação administrativa desabilitada")
    if not _atualizar_prontidao().pronto or estado.assinador_recuperacao is None:
        raise HTTPException(status_code=503, detail="recuperação temporariamente indisponível")
    identidade = _identidade_sessao(request)
    if identidade is None:
        raise HTTPException(status_code=401, detail="autenticação corporativa obrigatória")
    request.state.admin_operador = _operador_identidade(identidade)
    request.state.admin_identidade = identidade
    if not identidade.mfa:
        raise HTTPException(status_code=403, detail="autenticação multifator obrigatória")
    if not papeis.intersection(identidade.papeis):
        raise HTTPException(status_code=403, detail="perfil sem permissão para esta operação")
    _exigir_csrf(request, identidade)
    return identidade


def exigir_recuperacao_operador(request: Request) -> admin_auth.IdentidadeAdmin:
    return _exigir_identidade_recuperacao(
        request, {admin_auth.PAPEL_RECUPERACAO_OPERADOR},
    )


def exigir_recuperacao_aprovador(request: Request) -> admin_auth.IdentidadeAdmin:
    return _exigir_identidade_recuperacao(
        request, {admin_auth.PAPEL_RECUPERACAO_APROVADOR},
    )


def exigir_recuperacao_leitura(request: Request) -> admin_auth.IdentidadeAdmin:
    return _exigir_identidade_recuperacao(request, {
        admin_auth.PAPEL_RECUPERACAO_OPERADOR,
        admin_auth.PAPEL_RECUPERACAO_APROVADOR,
        admin_auth.PAPEL_AUDITORIA,
    })


@app.on_event("startup")
def _startup() -> None:
    montar_estado()


@app.get("/admin/login")
async def admin_login() -> RedirectResponse:
    try:
        url, transacao = admin_auth.criar_inicio_login(estado.oidc)
    except admin_auth.ErroAutenticacao as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    resposta = RedirectResponse(url, status_code=302)
    resposta.set_cookie(
        admin_auth.COOKIE_LOGIN,
        transacao,
        max_age=admin_auth.TTL_LOGIN,
        httponly=True,
        secure=_cookie_seguro(),
        samesite="lax",
        path="/admin",
    )
    return resposta


@app.get("/admin/callback")
async def admin_callback(request: Request) -> RedirectResponse:
    erro = request.query_params.get("error", "")
    codigo = request.query_params.get("code", "")
    state_recebido = request.query_params.get("state", "")
    if erro or not codigo or not state_recebido:
        raise HTTPException(status_code=400, detail="autenticação administrativa recusada")
    try:
        transacao = admin_auth.validar_inicio_login(
            request.cookies.get(admin_auth.COOKIE_LOGIN, ""), state_recebido, estado.oidc,
        )
        id_token = await run_in_threadpool(
            admin_auth.trocar_codigo,
            codigo,
            str(transacao["verifier"]),
            estado.oidc,
        )
        claims = await run_in_threadpool(
            admin_auth.validar_id_token,
            id_token,
            str(transacao["nonce"]),
            estado.oidc,
        )
        identidade = admin_auth.identidade_das_claims(claims, estado.oidc)
        cookie = admin_auth.criar_cookie_sessao(identidade, estado.oidc.session_secret)
    except admin_auth.ErroAutenticacao as exc:
        _log.warning("callback OIDC recusado: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail=str(exc))
    resposta = RedirectResponse("/admin", status_code=303)
    resposta.delete_cookie(admin_auth.COOKIE_LOGIN, path="/admin")
    resposta.set_cookie(
        admin_auth.COOKIE_SESSAO,
        cookie,
        max_age=max(1, identidade.expira_em - int(time.time())),
        httponly=True,
        secure=_cookie_seguro(),
        samesite="strict",
        path="/admin",
    )
    return resposta


@app.get("/admin/sessao")
async def admin_sessao(request: Request) -> dict[str, Any]:
    identidade = _identidade_sessao(request)
    if identidade is None:
        return {
            "ok": True,
            "autenticado": False,
            "oidc_habilitado": estado.oidc.configurado,
            "recuperacao_habilitada": estado.recuperacao_habilitada,
        }
    return {
        "ok": True,
        "autenticado": True,
        "oidc_habilitado": estado.oidc.configurado,
        "recuperacao_habilitada": estado.recuperacao_habilitada,
        "nome": identidade.nome,
        "papeis": sorted(identidade.papeis),
        "mfa": identidade.mfa,
        "csrf": identidade.csrf,
        "expira_em": identidade.expira_em,
    }


@app.post("/admin/logout")
async def admin_logout(request: Request) -> JSONResponse:
    identidade = _identidade_sessao(request)
    if identidade is not None:
        _exigir_csrf(request, identidade)
        request.state.admin_operador = _operador_identidade(identidade)
    resposta = JSONResponse({"ok": True})
    resposta.delete_cookie(admin_auth.COOKIE_SESSAO, path="/admin")
    resposta.delete_cookie(admin_auth.COOKIE_LOGIN, path="/admin")
    return resposta


# ─── Consulta (o app do auditor) ─────────────────────────────────────────────


def _constante_json_invalida(valor: str) -> None:
    raise ValueError(f"constante JSON inválida: {valor}")


def _objeto_json_sem_chaves_duplicadas(pares):
    resultado = {}
    for chave, valor in pares:
        if chave in resultado:
            raise ValueError("chave JSON duplicada")
        resultado[chave] = valor
    return resultado


async def _ler_objeto_json(
    request: Request, *, limite: int, publico: bool = False,
) -> dict[str, Any]:
    tipo = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if tipo != "application/json" and not tipo.endswith("+json"):
        raise HTTPException(status_code=400, detail="Content-Type application/json obrigatório")
    tamanho_declarado = request.headers.get("content-length", "").strip()
    if tamanho_declarado:
        try:
            tamanho = int(tamanho_declarado)
            if tamanho < 0:
                raise ValueError
            if tamanho > limite:
                raise HTTPException(status_code=400, detail="corpo JSON excede o tamanho permitido")
        except ValueError:
            raise HTTPException(status_code=400, detail="Content-Length inválido")
    partes: list[bytes] = []
    total = 0
    async for parte in request.stream():
        total += len(parte)
        if total > limite:
            raise HTTPException(status_code=400, detail="corpo JSON excede o tamanho permitido")
        partes.append(parte)
    try:
        corpo = json.loads(
            b"".join(partes).decode("utf-8"),
            parse_constant=_constante_json_invalida,
            object_pairs_hook=_objeto_json_sem_chaves_duplicadas,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        detalhe = "requisição inválida" if publico else "JSON inválido"
        raise HTTPException(status_code=400, detail=detalhe)
    if not isinstance(corpo, dict):
        detalhe = "requisição inválida" if publico else "corpo deve ser um objeto JSON"
        raise HTTPException(status_code=400, detail=detalhe)
    return corpo


def _entrada_admin(funcao, *args, **kwargs):
    try:
        return funcao(*args, **kwargs)
    except validacao.EntradaInvalida as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _validar_limite_listagem(limite: int) -> int:
    if not 1 <= limite <= 500:
        raise HTTPException(status_code=400, detail="limite deve estar entre 1 e 500")
    return limite


@app.post("/v1/consulta")
async def consulta(request: Request) -> JSONResponse:
    if not _atualizar_prontidao().pronto:
        raise HTTPException(status_code=503, detail="serviço temporariamente indisponível")
    corpo = await _ler_objeto_json(
        request, limite=validacao.MAX_CORPO_CONSULTA, publico=True,
    )
    try:
        req = contrato.extrair_requisicao(corpo)
    except validacao.EntradaInvalida as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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


def exigir_admin(
    request: Request,
    authorization: str = Header(default=""),
    x_admin_operador: str = Header(default="", alias="X-Admin-Operador"),
) -> None:
    if not _atualizar_prontidao().pronto:
        raise HTTPException(status_code=503, detail="serviço temporariamente indisponível")
    identidade = _identidade_sessao(request)
    if identidade is not None:
        if not identidade.mfa or not identidade.possui(admin_auth.PAPEL_LICENCAS):
            raise HTTPException(status_code=403, detail="perfil administrativo insuficiente")
        _exigir_csrf(request, identidade)
        request.state.admin_operador = _operador_identidade(identidade)
        request.state.admin_identidade = identidade
        return
    esperado = estado.admin_token
    if not esperado:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN não configurado")
    prefixo = "Bearer "
    recebido = authorization[len(prefixo):] if authorization.startswith(prefixo) else ""
    if not hmac.compare_digest(recebido, esperado):
        raise HTTPException(status_code=401, detail="token administrativo inválido")
    if estado.cfg.ambiente == contrato.AMBIENTE_PRODUCAO and not x_admin_operador.strip():
        # O token prova que a credencial é válida; a ausência do operador em
        # produção é uma recusa de autorização/auditoria, não um JSON malformado.
        raise HTTPException(
            status_code=403,
            detail="identificação do operador administrativo obrigatória",
        )
    operador = _entrada_admin(
        validacao.operador,
        x_admin_operador,
        obrigatorio=estado.cfg.ambiente == contrato.AMBIENTE_PRODUCAO,
    )
    request.state.admin_operador = operador or "token-compartilhado-homologacao"


@app.middleware("http")
async def auditar_requisicao_admin(request: Request, call_next):
    """Registra ações autenticadas sem guardar token, corpo ou dados pessoais."""
    try:
        resposta = await call_next(request)
    except Exception as exc:
        # O middleware é a última barreira para impedir que uma falha de banco
        # ou programação vaze seu texto no corpo HTTP.
        _log.error(
            "erro interno metodo=%s rota=%s tipo=%s",
            request.method, request.url.path, type(exc).__name__,
        )
        resposta = JSONResponse({"detail": "erro interno"}, status_code=500)
    operador = getattr(request.state, "admin_operador", "")
    if operador and request.url.path.startswith("/admin/"):
        try:
            estado.repo.registrar_auditoria_admin(
                operador=operador,
                acao=request.method,
                alvo=request.url.path,
                resultado=str(resposta.status_code),
                detalhes="",
                quando=datetime.now(timezone.utc),
            )
        except Exception:
            _log.exception("falha ao registrar auditoria administrativa")
    if request.url.path.startswith("/admin"):
        resposta.headers["Cache-Control"] = "no-store, max-age=0"
        resposta.headers["Pragma"] = "no-cache"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["X-Frame-Options"] = "DENY"
        resposta.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
    return resposta


@app.post("/admin/autorizar", dependencies=[Depends(exigir_admin)])
async def autorizar(request: Request) -> dict[str, Any]:
    if estado.repo.migracao_legado_concluida():
        raise HTTPException(
            status_code=409,
            detail="migração concluída; use uma licença contratual para novas instalações",
        )
    corpo = await _ler_objeto_json(request, limite=validacao.MAX_CORPO_ADMIN)
    _entrada_admin(
        validacao.validar_campos, corpo,
        {"instalacao_id", "codigo_ibge", "max_usuarios"},
        obrigatorios={"instalacao_id", "codigo_ibge"},
    )
    inst = _entrada_admin(validacao.uuid_canonico, corpo.get("instalacao_id"))
    ibge = _entrada_admin(validacao.codigo_ibge, corpo.get("codigo_ibge"))
    maxu = _entrada_admin(
        validacao.inteiro, corpo.get("max_usuarios"), "max_usuarios",
        maximo=validacao.MAX_AUDITORES, opcional=True,
    )
    try:
        estado.repo.autorizar(inst, ibge, maxu)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _log.info("autorizada instalacao=%s ibge=%s max=%s", inst, ibge, maxu)
    return {"ok": True, "instalacao_id": inst}


@app.post("/admin/revogar", dependencies=[Depends(exigir_admin)])
async def revogar(request: Request) -> dict[str, Any]:
    corpo = await _ler_objeto_json(request, limite=validacao.MAX_CORPO_ADMIN)
    _entrada_admin(
        validacao.validar_campos, corpo, {"instalacao_id"},
        obrigatorios={"instalacao_id"},
    )
    inst = _entrada_admin(validacao.uuid_canonico, corpo.get("instalacao_id"))
    existente = estado.repo.obter_instalacao(inst)
    if existente and existente.get("licenca_id"):
        agora = datetime.now(timezone.utc)
        estado.repo.associar_instalacao(
            inst, str(existente["licenca_id"]), "revogada", agora,
        )
        estado.repo.registrar_evento_licenca(
            str(existente["licenca_id"]), "instalacao_revogada",
            json.dumps({"instalacao_id": inst}, ensure_ascii=False, sort_keys=True), agora,
        )
        achou = True
    else:
        achou = estado.repo.revogar_instalacao(inst)
    _log.info("revogada instalacao=%s achou=%s", inst, achou)
    return {"ok": True, "revogada": achou}


@app.post("/admin/revogar-municipio", dependencies=[Depends(exigir_admin)])
async def revogar_municipio(request: Request) -> dict[str, Any]:
    corpo = await _ler_objeto_json(request, limite=validacao.MAX_CORPO_ADMIN)
    _entrada_admin(
        validacao.validar_campos, corpo, {"codigo_ibge"},
        obrigatorios={"codigo_ibge"},
    )
    ibge = _entrada_admin(validacao.codigo_ibge, corpo.get("codigo_ibge"))
    estado.repo.revogar_municipio(ibge)
    _log.info("revogado municipio ibge=%s", ibge)
    return {"ok": True, "codigo_ibge": ibge}


@app.post("/admin/reativar-municipio", dependencies=[Depends(exigir_admin)])
async def reativar_municipio(request: Request) -> dict[str, Any]:
    corpo = await _ler_objeto_json(request, limite=validacao.MAX_CORPO_ADMIN)
    _entrada_admin(
        validacao.validar_campos, corpo, {"codigo_ibge"},
        obrigatorios={"codigo_ibge"},
    )
    ibge = _entrada_admin(validacao.codigo_ibge, corpo.get("codigo_ibge"))
    estado.repo.reativar_municipio(ibge)
    _log.info("reativado municipio ibge=%s", ibge)
    return {"ok": True, "codigo_ibge": ibge}


@app.get("/admin/instalacoes", dependencies=[Depends(exigir_admin)])
async def listar() -> dict[str, Any]:
    return {"ok": True, "instalacoes": estado.repo.listar_instalacoes()}


@app.get("/admin/municipios-revogados", dependencies=[Depends(exigir_admin)])
async def listar_municipios_revogados() -> dict[str, Any]:
    return {"ok": True, "codigos": estado.repo.listar_municipios_revogados()}


@app.get("/admin/tentativas", dependencies=[Depends(exigir_admin)])
async def listar_tentativas(limite: int = 100) -> dict[str, Any]:
    """Consultas recentes (quem consultou, quando, status). Sem dado pessoal."""
    return {
        "ok": True,
        "tentativas": estado.repo.listar_tentativas(_validar_limite_listagem(limite)),
    }


@app.get("/admin/auditoria", dependencies=[Depends(exigir_admin)])
async def listar_auditoria(limite: int = 100) -> dict[str, Any]:
    return {
        "ok": True,
        "eventos": estado.repo.listar_auditoria_admin(_validar_limite_listagem(limite)),
    }


@app.get("/admin/migracao-legado/status", dependencies=[Depends(exigir_admin)])
async def status_migracao_legado() -> dict[str, Any]:
    return {"ok": True, "concluida": estado.repo.migracao_legado_concluida()}


@app.get("/admin/contexto", dependencies=[Depends(exigir_admin)])
async def contexto_admin() -> dict[str, Any]:
    return {
        "ok": True,
        "ambiente": estado.cfg.ambiente,
        "recuperacao_habilitada": estado.recuperacao_habilitada,
    }


# ─── Recuperação administrativa de senha ────────────────────────────────────


_REQUEST_ID_RECUPERACAO = re.compile(r"^[A-Za-z0-9_-]{10,256}$")
_ESTADOS_RECUPERACAO = {"preparada", "aprovada", "emitida", "expirada", "recusada"}
_LIMITE_CORPO_RECUPERACAO = 20 * 1024


def _request_id_recuperacao(valor: str) -> str:
    if not isinstance(valor, str) or not _REQUEST_ID_RECUPERACAO.fullmatch(valor):
        raise HTTPException(status_code=400, detail="request_id inválido")
    return valor


def _contexto_recuperacao(pedido: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    instalacao = estado.repo.obter_instalacao(str(pedido["installation_id"]))
    licenca_id = str((instalacao or {}).get("licenca_id") or "")
    licenca = estado.repo.obter_licenca(licenca_id) if licenca_id else None
    return instalacao, licenca


def _registro_recuperacao_publico(item: dict[str, Any]) -> dict[str, Any]:
    agora = int(time.time())
    estado_item = str(item.get("estado") or "")
    if estado_item in {"preparada", "aprovada"} and int(
        item.get("solicitacao_expira_em") or 0
    ) < agora:
        estado_item = "expirada"
    return {
        "request_id": item.get("request_id"),
        "installation_id": item.get("installation_id"),
        "usuario_mascarado": recuperacao_senha.mascarar_referencia(
            str(item.get("usuario_referencia") or "")
        ),
        "versao_app": item.get("versao_app"),
        "solicitado_em": item.get("solicitado_em"),
        "solicitacao_expira_em": item.get("solicitacao_expira_em"),
        "estado": estado_item,
        "operador_preparou": item.get("operador_preparou"),
        "aprovador": item.get("aprovador"),
        "emitido_por": item.get("emitido_por"),
        "protocolo": item.get("protocolo"),
        "justificativa": item.get("justificativa"),
        "metodo_verificacao": item.get("metodo_verificacao"),
        "canal_oficial_confirmado": item.get("canal_oficial_confirmado"),
        "escalonamento_confirmado": item.get("escalonamento_confirmado"),
        "kid": item.get("kid"),
        "emitido_em": item.get("emitido_em"),
        "token_expira_em": item.get("token_expira_em"),
        "criado_em": item.get("criado_em"),
        "atualizado_em": item.get("atualizado_em"),
    }


def _auditar_recuperacao(
    operador: str, acao: str, resultado: str, request_id: str, **detalhes: Any,
) -> None:
    saneado = {
        "request_id": request_id,
        **{
            chave: valor for chave, valor in detalhes.items()
            if chave not in {"solicitacao", "token", "desafio", "assinatura"}
        },
    }
    try:
        estado.repo.registrar_auditoria_admin(
            operador=operador,
            acao=acao,
            alvo="recuperacao_senha",
            resultado=resultado,
            detalhes=json.dumps(saneado, ensure_ascii=False, sort_keys=True),
            quando=datetime.now(timezone.utc),
        )
    except Exception:
        _log.exception("falha ao registrar evento de recuperação")


def _validar_pedido_recuperacao(corpo: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        validacao.validar_campos(
            corpo, {"solicitacao"}, obrigatorios={"solicitacao"},
        )
        texto = corpo["solicitacao"]
        if not isinstance(texto, str):
            raise recuperacao_senha.RecuperacaoInvalida("Solicitação inválida ou expirada.")
        return texto.strip(), recuperacao_senha.validar_solicitacao(texto)
    except (validacao.EntradaInvalida, recuperacao_senha.RecuperacaoInvalida) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/admin/recuperacoes/validar")
async def validar_recuperacao_admin(
    request: Request,
    identidade: admin_auth.IdentidadeAdmin = Depends(exigir_recuperacao_operador),
) -> dict[str, Any]:
    corpo = await _ler_objeto_json(request, limite=_LIMITE_CORPO_RECUPERACAO)
    _, pedido = _validar_pedido_recuperacao(corpo)
    instalacao, licenca = _contexto_recuperacao(pedido)
    resumo = recuperacao_senha.resumo_solicitacao(pedido, instalacao, licenca)
    _auditar_recuperacao(
        _operador_identidade(identidade), "recuperacao_validada", "ok",
        str(pedido["request_id"]), installation_id=pedido["installation_id"],
        instalacao_conhecida=instalacao is not None,
    )
    return {"ok": True, "solicitacao": resumo}


@app.post("/admin/recuperacoes/preparar")
async def preparar_recuperacao_admin(
    request: Request,
    identidade: admin_auth.IdentidadeAdmin = Depends(exigir_recuperacao_operador),
) -> dict[str, Any]:
    corpo = await _ler_objeto_json(request, limite=_LIMITE_CORPO_RECUPERACAO)
    try:
        validacao.validar_campos(corpo, {
            "solicitacao", "protocolo", "justificativa", "metodo_verificacao",
            "canal_oficial_confirmado", "escalonamento_confirmado",
        }, obrigatorios={
            "solicitacao", "protocolo", "justificativa", "metodo_verificacao",
            "canal_oficial_confirmado",
        })
        texto = corpo.get("solicitacao")
        if not isinstance(texto, str):
            raise recuperacao_senha.RecuperacaoInvalida("Solicitação inválida ou expirada.")
        pedido = recuperacao_senha.validar_solicitacao(texto)
        instalacao, _ = _contexto_recuperacao(pedido)
        agora = int(time.time())
        operador = _operador_identidade(identidade)
        contagem_operador, contagem_instalacao = estado.repo.contar_recuperacoes_recentes(
            operador,
            str(pedido["installation_id"]),
            agora - estado.recuperacao_rate_janela_s,
        )
        if max(contagem_operador, contagem_instalacao) >= estado.recuperacao_rate_max:
            _auditar_recuperacao(
                operador, "recuperacao_limite_excedido", "recusada",
                str(pedido["request_id"]), installation_id=pedido["installation_id"],
            )
            raise HTTPException(status_code=429, detail="limite de recuperações atingido")
        registro = recuperacao_senha.registro_preparado(
            texto.strip(),
            pedido,
            operador=operador,
            protocolo=corpo.get("protocolo"),
            justificativa=corpo.get("justificativa"),
            metodo_verificacao=corpo.get("metodo_verificacao"),
            canal_oficial_confirmado=corpo.get("canal_oficial_confirmado"),
            escalonamento_confirmado=corpo.get("escalonamento_confirmado") is True,
            instalacao_conhecida=instalacao is not None,
            agora=agora,
        )
        salvo = estado.repo.preparar_recuperacao(registro)
    except HTTPException:
        raise
    except RecuperacaoDuplicada as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (validacao.EntradaInvalida, recuperacao_senha.RecuperacaoInvalida) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _auditar_recuperacao(
        operador, "recuperacao_preparada", "ok", str(pedido["request_id"]),
        installation_id=pedido["installation_id"], protocolo=salvo["protocolo"],
        metodo_verificacao=salvo["metodo_verificacao"],
    )
    return {"ok": True, "recuperacao": _registro_recuperacao_publico(salvo)}


@app.post("/admin/recuperacoes/{request_id}/aprovar")
async def aprovar_recuperacao_admin(
    request_id: str,
    request: Request,
    identidade: admin_auth.IdentidadeAdmin = Depends(exigir_recuperacao_aprovador),
) -> dict[str, Any]:
    request_id = _request_id_recuperacao(request_id)
    corpo = await _ler_objeto_json(request, limite=1024)
    if set(corpo) != {"confirmar"} or corpo.get("confirmar") is not True:
        raise HTTPException(status_code=422, detail="confirmação explícita obrigatória")
    operador = _operador_identidade(identidade)
    try:
        item = estado.repo.aprovar_recuperacao(
            request_id, operador, int(time.time()), estado.recuperacao_dupla_aprovacao,
        )
    except RecuperacaoAutoaprovacao as exc:
        _auditar_recuperacao(operador, "recuperacao_autoaprovacao", "recusada", request_id)
        raise HTTPException(status_code=409, detail=str(exc))
    except RecuperacaoEstadoInvalido as exc:
        codigo = 410 if "expirou" in str(exc).lower() else 409
        raise HTTPException(status_code=codigo, detail=str(exc))
    if item is None:
        raise HTTPException(status_code=404, detail="solicitação não encontrada")
    _auditar_recuperacao(operador, "recuperacao_aprovada", "ok", request_id)
    return {"ok": True, "recuperacao": _registro_recuperacao_publico(item)}


@app.post("/admin/recuperacoes/{request_id}/emitir")
async def emitir_recuperacao_admin(
    request_id: str,
    request: Request,
    identidade: admin_auth.IdentidadeAdmin = Depends(exigir_recuperacao_aprovador),
) -> dict[str, Any]:
    request_id = _request_id_recuperacao(request_id)
    corpo = await _ler_objeto_json(request, limite=_LIMITE_CORPO_RECUPERACAO)
    try:
        validacao.validar_campos(
            corpo, {"solicitacao", "confirmacao"},
            obrigatorios={"solicitacao", "confirmacao"},
        )
        if corpo.get("confirmacao") != "EMITIR":
            raise recuperacao_senha.RecuperacaoInvalida("Digite EMITIR para confirmar.")
        texto = corpo.get("solicitacao")
        if not isinstance(texto, str):
            raise recuperacao_senha.RecuperacaoInvalida("Solicitação inválida ou expirada.")
        pedido = recuperacao_senha.validar_solicitacao(texto)
        if not hmac.compare_digest(str(pedido["request_id"]), request_id):
            raise recuperacao_senha.RecuperacaoInvalida("A solicitação não corresponde ao registro.")
        registro = estado.repo.obter_recuperacao(request_id)
        if registro is None:
            raise HTTPException(status_code=404, detail="solicitação não encontrada")
        operador = _operador_identidade(identidade)
        if not hmac.compare_digest(str(registro.get("aprovador") or ""), operador):
            raise recuperacao_senha.RecuperacaoInvalida(
                "Somente quem aprovou pode concluir esta emissão."
            )
        if estado.assinador_recuperacao is None:
            raise HTTPException(status_code=503, detail="chave de recuperação indisponível")
        agora = int(time.time())
        token, payload = recuperacao_senha.emitir_token(
            pedido,
            estado.assinador_recuperacao,
            agora=agora,
            ttl_segundos=estado.recuperacao_token_ttl_s,
        )
        confirmado = estado.repo.confirmar_emissao_recuperacao(
            request_id=request_id,
            request_digest=recuperacao_senha.digest_texto(texto.strip()),
            emitido_por=operador,
            kid=str(payload["kid"]),
            jti_digest=recuperacao_senha.digest_texto(str(payload["jti"])),
            token_digest=recuperacao_senha.digest_texto(token),
            emitido_em=agora,
            token_expira_em=int(payload["exp"]),
        )
    except HTTPException:
        raise
    except RecuperacaoEstadoInvalido as exc:
        codigo = 410 if "expirou" in str(exc).lower() else 409
        raise HTTPException(status_code=codigo, detail=str(exc))
    except (validacao.EntradaInvalida, recuperacao_senha.RecuperacaoInvalida) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if confirmado is None:
        raise HTTPException(status_code=404, detail="solicitação não encontrada")
    _auditar_recuperacao(
        operador, "recuperacao_token_emitido", "ok", request_id,
        installation_id=pedido["installation_id"], kid=payload["kid"],
        token_expira_em=payload["exp"],
    )
    return {
        "ok": True,
        "request_id": request_id,
        "token": token,
        "expira_em": payload["exp"],
        "aviso": "O token não será exibido novamente.",
    }


@app.get("/admin/recuperacoes")
async def listar_recuperacoes_admin(
    request: Request,
    limite: int = 100,
    status: str = "",
    _identidade: admin_auth.IdentidadeAdmin = Depends(exigir_recuperacao_leitura),
) -> dict[str, Any]:
    limite = _validar_limite_listagem(limite)
    if status and status not in _ESTADOS_RECUPERACAO:
        raise HTTPException(status_code=400, detail="status de recuperação inválido")
    itens = estado.repo.listar_recuperacoes(limite, status)
    return {"ok": True, "recuperacoes": [_registro_recuperacao_publico(i) for i in itens]}


@app.get("/admin/recuperacoes/{request_id}")
async def obter_recuperacao_admin(
    request_id: str,
    request: Request,
    _identidade: admin_auth.IdentidadeAdmin = Depends(exigir_recuperacao_leitura),
) -> dict[str, Any]:
    request_id = _request_id_recuperacao(request_id)
    item = estado.repo.obter_recuperacao(request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="solicitação não encontrada")
    instalacao = estado.repo.obter_instalacao(str(item["installation_id"]))
    licenca_id = str((instalacao or {}).get("licenca_id") or "")
    licenca = estado.repo.obter_licenca(licenca_id) if licenca_id else None
    return {
        "ok": True,
        "recuperacao": _registro_recuperacao_publico(item),
        "contexto": {
            "instalacao_conhecida": instalacao is not None,
            "status_instalacao": (instalacao or {}).get("status_instalacao"),
            "ultima_consulta_em": (instalacao or {}).get("ultima_consulta_em"),
            "licenca_id": licenca_id or None,
            "codigo_ibge": (instalacao or {}).get("codigo_ibge"),
            "nome_municipio": (licenca or {}).get("nome_municipio"),
            "status_licenca": (licenca or {}).get("status"),
        },
    }


# ─── Administração contratual (Etapa 3) ────────────────────────────────────


@app.post("/admin/licencas", dependencies=[Depends(exigir_admin)])
async def criar_licenca(request: Request) -> dict[str, Any]:
    try:
        corpo = await _ler_objeto_json(request, limite=validacao.MAX_CORPO_ADMIN)
        if "licenca_id" in corpo:
            raise licencas.LicencaInvalida("licenca_id é gerado pelo serviço")
        dados = licencas.normalizar_criacao(corpo)
        criada = estado.repo.criar_licenca(dados)
        agora = datetime.now(timezone.utc)
        estado.repo.registrar_evento_licenca(
            criada["licenca_id"], "licenca_criada",
            json.dumps({
                "codigo_ibge": criada["codigo_ibge"],
                "nome_municipio": criada.get("nome_municipio") or "",
                "status": criada["status"],
                "expira_em": criada["expira_em"], "max_auditores": criada["max_auditores"],
            }, ensure_ascii=False, sort_keys=True),
            agora,
        )
        _log.info("licenca criada id=%s ibge=%s", criada["licenca_id"], criada["codigo_ibge"])
        return {"ok": True, "licenca": criada}
    except (licencas.LicencaInvalida, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/admin/licencas", dependencies=[Depends(exigir_admin)])
async def listar_licencas() -> dict[str, Any]:
    return {"ok": True, "licencas": estado.repo.listar_licencas()}


@app.patch("/admin/licencas/{licenca_id}", dependencies=[Depends(exigir_admin)])
async def atualizar_licenca(licenca_id: str, request: Request) -> dict[str, Any]:
    licenca_id = _entrada_admin(validacao.uuid_canonico, licenca_id, "licenca_id")
    existente = estado.repo.obter_licenca(licenca_id)
    if existente is None:
        raise HTTPException(status_code=404, detail="licença não encontrada")
    try:
        corpo = await _ler_objeto_json(request, limite=validacao.MAX_CORPO_ADMIN)
        confirmar = corpo.pop("confirmar_encurtamento_vigencia", False)
        if not isinstance(confirmar, bool):
            raise licencas.LicencaInvalida(
                "confirmar_encurtamento_vigencia deve ser verdadeiro ou falso"
            )
        dados = licencas.normalizar_atualizacao(
            existente, corpo, confirmar_encurtamento=confirmar,
        )
        atualizada = estado.repo.atualizar_licenca(licenca_id, dados)
        if atualizada is None:
            raise HTTPException(status_code=404, detail="licença não encontrada")
        estado.repo.registrar_evento_licenca(
            licenca_id, "licenca_atualizada",
            json.dumps({
                "campos": sorted(corpo),
                "status_anterior": existente.get("status"),
                "status_atual": atualizada.get("status"),
                "expira_em": atualizada.get("expira_em"),
            }, ensure_ascii=False, sort_keys=True),
            datetime.now(timezone.utc),
        )
        _log.info("licenca atualizada id=%s campos=%s", licenca_id, sorted(corpo))
        return {"ok": True, "licenca": atualizada}
    except (licencas.LicencaInvalida, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/admin/licencas/{licenca_id}/instalacoes", dependencies=[Depends(exigir_admin)])
async def associar_instalacao(licenca_id: str, request: Request) -> dict[str, Any]:
    try:
        licenca_id = _entrada_admin(validacao.uuid_canonico, licenca_id, "licenca_id")
        corpo = await _ler_objeto_json(request, limite=validacao.MAX_CORPO_ADMIN)
        vinculo = licencas.normalizar_instalacao(corpo)
        anterior = estado.repo.obter_instalacao(vinculo["instalacao_id"])
        licenca_anterior_id = str((anterior or {}).get("licenca_id") or "")
        transferencia = bool(licenca_anterior_id and licenca_anterior_id != licenca_id)
        if transferencia and not vinculo["confirmar_transferencia"]:
            licenca_anterior = estado.repo.obter_licenca(licenca_anterior_id) or {}
            fim_anterior = str(licenca_anterior.get("expira_em") or "não informada")
            raise HTTPException(
                status_code=409,
                detail=(
                    "a instalação já pertence a outra licença; a licença anterior "
                    f"continua válida até {fim_anterior}. Envie "
                    "confirmar_transferencia=true para confirmar a alteração"
                ),
            )
        agora = datetime.now(timezone.utc)
        instalacao = estado.repo.associar_instalacao(
            vinculo["instalacao_id"], licenca_id, vinculo["status"], agora
        )
        detalhes_vinculo = {
            "instalacao_id": vinculo["instalacao_id"],
            "status": vinculo["status"],
        }
        if transferencia:
            estado.repo.registrar_evento_licenca(
                licenca_anterior_id,
                "instalacao_transferida_saida",
                json.dumps({
                    **detalhes_vinculo,
                    "licenca_destino_id": licenca_id,
                }, ensure_ascii=False, sort_keys=True),
                agora,
            )
        estado.repo.registrar_evento_licenca(
            licenca_id,
            "instalacao_transferida_entrada" if transferencia else "instalacao_associada",
            json.dumps({
                **detalhes_vinculo,
                **({"licenca_origem_id": licenca_anterior_id} if transferencia else {}),
            }, ensure_ascii=False, sort_keys=True),
            agora,
        )
        _log.info(
            "instalacao associada id=%s licenca=%s status=%s transferencia=%s",
            vinculo["instalacao_id"], licenca_id, vinculo["status"], transferencia,
        )
        return {
            "ok": True,
            "instalacao": instalacao,
            "transferida": transferencia,
            "licenca_anterior_id": licenca_anterior_id or None,
        }
    except (licencas.LicencaInvalida, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/admin/licencas/{licenca_id}/historico", dependencies=[Depends(exigir_admin)])
async def historico_licenca(licenca_id: str, limite: int = 100) -> dict[str, Any]:
    licenca_id = _entrada_admin(validacao.uuid_canonico, licenca_id, "licenca_id")
    limite = _validar_limite_listagem(limite)
    if estado.repo.obter_licenca(licenca_id) is None:
        raise HTTPException(status_code=404, detail="licença não encontrada")
    return {
        "ok": True,
        "eventos": estado.repo.listar_eventos_licenca(licenca_id, limite),
    }


@app.get("/admin", response_class=HTMLResponse)
async def painel_admin() -> HTMLResponse:
    """Shell público; dados exigem sessão OIDC ou credencial legada permitida."""
    return HTMLResponse(PAGINA_ADMIN)


# ─── Saúde ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/ready")
async def ready() -> JSONResponse:
    pronto = _atualizar_prontidao().pronto
    return JSONResponse({"ok": pronto}, status_code=200 if pronto else 503)
