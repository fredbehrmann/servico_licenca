# -*- coding: utf-8 -*-
"""Contrato de dados da licença — CÓPIA VERBATIM do SICOF.

⚠️  Estas funções precisam ficar **byte a byte idênticas** a
``SICOF_NEXT/app/licenca_modelos.py``. O cliente (no app do auditor) assina/confere
sobre exatamente esta serialização canônica; qualquer divergência — uma vírgula no
``separators``, ``ensure_ascii``, um campo a mais — faz o app rejeitar a licença.

Se um dia o contrato mudar no SICOF, mude aqui junto e rode a verificação de
compatibilidade (README, seção "Compatibilidade").
"""

from __future__ import annotations

import json
from typing import Any

VERSAO_CONTRATO = 1

CAMPOS_REQUISICAO = frozenset({"instalacao_id", "codigo_ibge", "versao_app", "nonce"})

STATUS_VALIDOS = frozenset({
    "ativa", "expirada", "revogada", "instalacao_nao_autorizada", "erro_temporario",
})

AMBIENTE_HOMOLOGACAO = "homologacao"
AMBIENTE_PRODUCAO = "producao"

# Campos da resposta assinada, na ordem lógica (a ordem não importa: sort_keys
# canoniza). A assinatura cobre todos MENOS ela própria.
CAMPOS_RESPOSTA = (
    "status", "codigo_ibge", "instalacao_id", "nonce", "emitida_em", "expira_em",
    "offline_ate", "max_usuarios", "versao_minima", "key_id", "ambiente",
)


def canonico(d: dict[str, Any]) -> bytes:
    """Idêntico a licenca_modelos._canonico do SICOF."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def payload_para_assinar(resposta: dict[str, Any]) -> bytes:
    """Idêntico a licenca_modelos.payload_para_assinar do SICOF."""
    sem_assinatura = {k: v for k, v in resposta.items() if k != "assinatura"}
    return canonico(sem_assinatura)


def extrair_requisicao(corpo: dict[str, Any]) -> dict[str, str]:
    """Só os quatro campos, como strings. Ignora qualquer extra recebido.

    Levanta ValueError se algum dos quatro faltar ou vier vazio.
    """
    faltando = [c for c in CAMPOS_REQUISICAO if not str(corpo.get(c) or "").strip()]
    if faltando:
        raise ValueError(f"campos ausentes na requisição: {sorted(faltando)}")
    return {c: str(corpo[c]).strip() for c in CAMPOS_REQUISICAO}
