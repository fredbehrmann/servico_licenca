# -*- coding: utf-8 -*-
"""Catálogo oficial de municípios usado no cadastro e na habilitação trial."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").encode(
        "ascii", "ignore"
    ).decode("ascii").upper().strip()


@lru_cache(maxsize=1)
def catalogo() -> dict[str, tuple[str, str]]:
    caminho = Path(__file__).with_name("ecd_municipios_ibge.json")
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(bruto, dict):
        raise RuntimeError("catálogo IBGE inválido")
    return {
        str(codigo): (str(dados[0]), str(dados[1]))
        for codigo, dados in bruto.items()
        if isinstance(dados, list) and len(dados) == 2
    }


def obter(codigo_ibge: str) -> dict[str, str] | None:
    codigo = re.sub(r"\D", "", str(codigo_ibge or ""))
    dados = catalogo().get(codigo)
    if not dados:
        return None
    return {"codigo_ibge": codigo, "nome": dados[0], "uf": dados[1]}


def buscar(termo: str, limite: int = 20) -> list[dict[str, str]]:
    texto = str(termo or "").strip()
    if len(texto) < 2:
        return []
    maximo = max(1, min(int(limite or 20), 100))
    digitos = re.sub(r"\D", "", texto)
    normalizado = _normalizar(texto)
    candidatos: list[dict[str, str]] = []
    for codigo, (nome, uf) in catalogo().items():
        nome_normalizado = _normalizar(nome)
        if ((digitos and digitos in codigo)
                or (normalizado and normalizado in nome_normalizado)):
            candidatos.append({
                "codigo_ibge": codigo,
                "nome": nome,
                "uf": uf,
                "rotulo": f"{nome}/{uf} — {codigo}",
            })

    def chave(item: dict[str, str]) -> tuple[int, str, str]:
        nome = _normalizar(item["nome"])
        if digitos and item["codigo_ibge"] == digitos:
            prioridade = 0
        elif ((digitos and item["codigo_ibge"].startswith(digitos))
              or nome.startswith(normalizado)):
            prioridade = 1
        else:
            prioridade = 2
        return prioridade, nome, item["codigo_ibge"]

    candidatos.sort(key=chave)
    return candidatos[:maximo]
