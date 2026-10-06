# -*- coding: utf-8 -*-
"""Contrato ponta a ponta: pedido do SICOF → serviço → consumo no SICOF."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.assinador_recuperacao import AssinadorRecuperacao
from app.recuperacao_senha import emitir_token, validar_solicitacao


def _diretorio_sicof() -> Path:
    configurado = os.environ.get("SICOF_REPO_DIR", "").strip()
    candidatos = [Path(configurado)] if configurado else []
    candidatos.append(Path(__file__).resolve().parents[2] / "sicof")
    for candidato in candidatos:
        if (candidato / "SICOF_NEXT/app/recuperacao_acesso.py").is_file():
            return candidato
    if os.environ.get("CI"):
        pytest.fail("repositório SICOF não foi disponibilizado ao teste de recuperação")
    pytest.skip("repositório SICOF não encontrado")


def _executar_sicof(sicof: Path, programa: str, entrada: dict, ambiente: dict) -> dict:
    processo = subprocess.run(
        [sys.executable, "-c", programa],
        input=json.dumps(entrada),
        text=True,
        capture_output=True,
        cwd=sicof,
        env=ambiente,
        timeout=30,
        check=False,
    )
    assert processo.returncode == 0, processo.stderr
    return json.loads(processo.stdout)


def test_sicof_gera_pedido_servico_assina_e_sicof_troca_senha(tmp_path):
    sicof = _diretorio_sicof()
    privada = Ed25519PrivateKey.generate()
    pem = privada.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    publica = privada.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    kid = "recuperacao-contrato-teste"
    ambiente = os.environ.copy()
    ambiente["PYTHONPATH"] = str(sicof / "SICOF_NEXT")
    ambiente["SICOF_DATA_DIR"] = str(tmp_path / "dados-sicof")
    ambiente["SICOF_AMBIENTE"] = "desenvolvimento"
    ambiente["SICOF_RECUPERACAO_CHAVES_EXTRA"] = json.dumps({
        kid: base64.b64encode(publica).decode("ascii"),
    })

    gerada = _executar_sicof(sicof, r"""
import json, sys
from app import auth_usuarios, recuperacao_acesso
auth_usuarios.criar_primeiro_administrador(
    "admin@contrato.test", "senha-inicial-contrato", "Administrador",
)
referencia = recuperacao_acesso.listar_administradores_mascarados()[0]["referencia"]
print(json.dumps({"solicitacao": recuperacao_acesso.criar_solicitacao(referencia)}))
""", {}, ambiente)

    pedido = validar_solicitacao(gerada["solicitacao"])
    token, _ = emitir_token(pedido, AssinadorRecuperacao(pem, kid))

    consumida = _executar_sicof(sicof, r"""
import json, sys
from app import auth_usuarios, recuperacao_acesso
entrada = json.loads(sys.stdin.read())
handle = recuperacao_acesso.validar_token(entrada["token"])
recuperacao_acesso.concluir(handle, "senha-nova-contrato-segura")
print(json.dumps({
    "autenticou": bool(auth_usuarios.autenticar(
        "admin@contrato.test", "senha-nova-contrato-segura"
    ))
}))
""", {"token": token}, ambiente)
    assert consumida == {"autenticou": True}

