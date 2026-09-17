# -*- coding: utf-8 -*-
"""Validação da configuração operacional do serviço de licenças.

O processo pode permanecer vivo para que ``/health`` ajude no diagnóstico, mas
``/ready`` e a emissão de licenças só ficam disponíveis quando todas as
condições indispensáveis do ambiente são atendidas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app import contrato
from app.assinador import Assinador
from app.repositorio import Repositorio, RepositorioPostgres


AMBIENTES = {
    contrato.AMBIENTE_PRODUCAO,
    contrato.AMBIENTE_HOMOLOGACAO,
}
_VERSAO = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_TOKENS_FRACOS = {
    "admin", "changeme", "senha", "password", "secret", "token",
    "admin123", "techfisco",
}


@dataclass(frozen=True)
class ConfiguracaoOperacional:
    ambiente: str
    ambiente_explicito: bool
    database_url: str
    key_id: str
    publica_esperada_b64: str
    admin_token: str
    versao_minima: str
    dias_offline: int
    dias_validade: int
    rate_max: int
    rate_janela_s: int
    replicas: int
    replicas_explicitas: bool
    retencao_tentativas_dias: int
    retencao_auditoria_dias: int


@dataclass(frozen=True)
class DiagnosticoProntidao:
    pronto: bool
    problemas: tuple[str, ...]


def _url_postgres(valor: str) -> bool:
    return valor.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))


def _token_forte(valor: str) -> bool:
    texto = valor.strip()
    return len(texto) >= 32 and texto.lower() not in _TOKENS_FRACOS and len(set(texto)) >= 12


def diagnosticar(
    cfg: ConfiguracaoOperacional,
    repo: Repositorio,
    assinador: Optional[Assinador],
) -> DiagnosticoProntidao:
    """Valida configuração, chave, banco e esquema sem expor segredos."""
    problemas: list[str] = []

    if not cfg.ambiente_explicito:
        problemas.append("ambiente_nao_definido")
    elif cfg.ambiente not in AMBIENTES:
        problemas.append("ambiente_invalido")

    if not cfg.key_id:
        problemas.append("key_id_ausente")
    if assinador is None:
        problemas.append("chave_privada_invalida_ou_ausente")
    elif cfg.key_id != assinador.key_id:
        problemas.append("key_id_nao_corresponde_ao_assinador")
    elif cfg.publica_esperada_b64 and cfg.publica_esperada_b64 != assinador.chave_publica_b64():
        problemas.append("chave_privada_nao_corresponde_a_publica_esperada")
    if cfg.ambiente in AMBIENTES and not cfg.publica_esperada_b64:
        problemas.append("chave_publica_esperada_ausente")

    if not _VERSAO.fullmatch(cfg.versao_minima):
        problemas.append("versao_minima_invalida")
    if not 1 <= cfg.dias_offline <= 30:
        problemas.append("tolerancia_offline_invalida")
    if cfg.dias_validade < 1:
        problemas.append("validade_invalida")
    if cfg.rate_max < 1 or cfg.rate_janela_s < 1:
        problemas.append("limite_requisicoes_invalido")
    if cfg.retencao_tentativas_dias < 1 or cfg.retencao_auditoria_dias < 1:
        problemas.append("retencao_invalida")

    if cfg.ambiente == contrato.AMBIENTE_PRODUCAO:
        if not cfg.database_url or not _url_postgres(cfg.database_url):
            problemas.append("database_url_postgres_obrigatoria")
        if not isinstance(repo, RepositorioPostgres):
            problemas.append("repositorio_postgres_obrigatorio")
        if not _token_forte(cfg.admin_token):
            problemas.append("credencial_admin_fraca_ou_ausente")
        if not cfg.replicas_explicitas:
            problemas.append("quantidade_replicas_nao_definida")
        elif cfg.replicas != 1:
            problemas.append("limitador_local_exige_uma_replica")

    try:
        repo_pronto = repo.pronto()
    except Exception:
        repo_pronto = False
    if not repo_pronto:
        problemas.append("banco_ou_schema_indisponivel")

    return DiagnosticoProntidao(not problemas, tuple(dict.fromkeys(problemas)))
