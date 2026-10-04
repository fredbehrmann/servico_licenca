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
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import contrato, licencas, validacao
from app.painel import PAGINA_ADMIN
from app.assinador import Assinador, assinador_do_ambiente
from app.configuracao import (
    ConfiguracaoOperacional,
    DiagnosticoProntidao,
    diagnosticar,
)
from app.limite import LimitadorMemoria
from app.repositorio import Repositorio, RepositorioMemoria, repositorio_do_ambiente
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


estado = Estado()


def _int_env(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        return padrao
    try:
        return int(valor)
    except ValueError:
        return 0


def _atualizar_prontidao() -> DiagnosticoProntidao:
    estado.diagnostico = diagnosticar(estado.operacional, estado.repo, estado.assinador)
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


@app.on_event("startup")
def _startup() -> None:
    montar_estado()


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
    return {"ok": True, "ambiente": estado.cfg.ambiente}


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
    """Painel web de administração. A página é pública (só o formulário); as
    ações exigem o ADMIN_TOKEN, enviado do navegador aos endpoints /admin/*."""
    return HTMLResponse(PAGINA_ADMIN)


# ─── Saúde ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/ready")
async def ready() -> JSONResponse:
    pronto = _atualizar_prontidao().pronto
    return JSONResponse({"ok": pronto}, status_code=200 if pronto else 503)
