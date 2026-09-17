# -*- coding: utf-8 -*-
"""Contrato: canonização e extração da requisição."""

from __future__ import annotations

import pytest

from app import contrato
from tests.conftest import INSTALACAO_1


def test_canonico_ordena_e_compacta():
    b = contrato.canonico({"b": 2, "a": 1})
    assert b == b'{"a":1,"b":2}'


def test_payload_remove_assinatura():
    resp = {"status": "ativa", "assinatura": "XXX", "nonce": "n"}
    p = contrato.payload_para_assinar(resp)
    assert b"assinatura" not in p
    assert b"ativa" in p and b'"nonce":"n"' in p


def test_extrair_requisicao_ok():
    corpo = {"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408",
             "versao_app": "1.0.0", "nonce": "n"}
    req = contrato.extrair_requisicao(corpo)
    assert set(req) == contrato.CAMPOS_REQUISICAO


def test_extrair_requisicao_recusa_campo_extra():
    corpo = {"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408",
             "versao_app": "1.0.0", "nonce": "n", "extra": "recusado"}
    with pytest.raises(ValueError, match="não permitidos"):
        contrato.extrair_requisicao(corpo)


@pytest.mark.parametrize("faltando", ["instalacao_id", "codigo_ibge", "versao_app", "nonce"])
def test_extrair_requisicao_recusa_campo_ausente(faltando):
    corpo = {"instalacao_id": INSTALACAO_1, "codigo_ibge": "2927408",
             "versao_app": "1.0.0", "nonce": "n"}
    corpo[faltando] = ""
    with pytest.raises(ValueError):
        contrato.extrair_requisicao(corpo)
