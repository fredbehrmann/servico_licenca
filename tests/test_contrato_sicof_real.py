# -*- coding: utf-8 -*-
"""Contrato ponta a ponta: API real do serviço → cliente real do SICOF."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import ADMIN, INSTALACAO_1, KEY_ID


def _diretorio_sicof() -> Path:
    configurado = os.environ.get("SICOF_REPO_DIR", "").strip()
    candidatos = [
        Path(configurado) if configurado else None,
        Path(__file__).resolve().parents[2] / "sicof",
    ]
    for candidato in candidatos:
        if candidato and (candidato / "SICOF_NEXT/app/licenca_cliente.py").is_file():
            return candidato
    if os.environ.get("CI"):
        pytest.fail("repositório SICOF não foi disponibilizado ao teste de contrato")
    pytest.skip("repositório SICOF não encontrado ao lado de servico_licenca")


def test_resposta_da_api_e_validada_pelo_cliente_real_do_sicof(
    cliente, tmp_path,
):
    c, publica, _ = cliente
    agora = datetime.now(timezone.utc)
    headers = {"Authorization": f"Bearer {ADMIN}"}
    criada = c.post("/admin/licencas", headers=headers, json={
        "codigo_ibge": "2927408",
        "status": "ativa",
        "inicio_em": (agora - timedelta(days=1)).isoformat(),
        "expira_em": (agora + timedelta(days=365)).isoformat(),
        "max_auditores": 7,
        "max_instalacoes_ativas": 1,
        "dias_offline": 7,
        "versao_minima": "1.0.0",
    })
    assert criada.status_code == 200
    licenca_id = criada.json()["licenca"]["licenca_id"]
    associada = c.post(
        f"/admin/licencas/{licenca_id}/instalacoes",
        headers=headers,
        json={"instalacao_id": INSTALACAO_1, "status": "ativa"},
    )
    assert associada.status_code == 200
    requisicao = {
        "instalacao_id": INSTALACAO_1,
        "codigo_ibge": "2927408",
        "versao_app": "1.0.0",
        "nonce": "nonce-contrato-entre-repositorios",
    }
    emitida = c.post("/v1/consulta", json=requisicao)
    assert emitida.status_code == 200
    resposta = emitida.json()
    assert resposta["ambiente"] == "homologacao"
    assert resposta["key_id"] == KEY_ID

    sicof = _diretorio_sicof()
    programa = r"""
import base64, json, sys
from datetime import datetime
from app.licenca_cliente import avaliar
from app.licenca_modelos import RequisicaoLicenca

entrada = json.loads(sys.stdin.read())
req = RequisicaoLicenca(**entrada["requisicao"])
agora = datetime.fromisoformat(entrada["resposta"]["emitida_em"])
publica = base64.b64decode(entrada["publica_b64"])
aceita, persistir = avaliar(
    entrada["resposta"], None, None, req, agora,
    {entrada["resposta"]["key_id"]: publica},
)
recusada, persistir_producao = avaliar(
    entrada["resposta"], None, None, req, agora,
    {"prod-ed25519-v1": bytes(32)},
)
print(json.dumps({
    "status": aceita["status"],
    "persistir": persistir,
    "key_id": aceita["key_id"],
    "nonce": aceita["nonce"],
    "codigo_ibge": aceita["codigo_ibge"],
    "instalacao_id": aceita["instalacao_id"],
    "max_usuarios": aceita["max_usuarios"],
    "status_cliente_producao": recusada["status"],
    "persistir_cliente_producao": persistir_producao,
}, sort_keys=True))
"""
    ambiente = os.environ.copy()
    ambiente["PYTHONPATH"] = str(sicof / "SICOF_NEXT")
    ambiente["SICOF_DATA_DIR"] = str(tmp_path / "dados-cliente")
    processo = subprocess.run(
        [sys.executable, "-c", programa],
        input=json.dumps({
            "requisicao": requisicao,
            "resposta": resposta,
            "publica_b64": base64.b64encode(publica).decode("ascii"),
        }),
        text=True,
        capture_output=True,
        env=ambiente,
        cwd=sicof,
        timeout=20,
        check=False,
    )
    assert processo.returncode == 0, processo.stderr
    validada = json.loads(processo.stdout)
    assert validada == {
        "codigo_ibge": requisicao["codigo_ibge"],
        "instalacao_id": requisicao["instalacao_id"],
        "key_id": KEY_ID,
        "max_usuarios": 7,
        "nonce": requisicao["nonce"],
        "persistir": True,
        "persistir_cliente_producao": False,
        "status": "ativa",
        "status_cliente_producao": "erro_temporario",
    }
