# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app import admin_auth, recuperacao_contrato
from app.recuperacao_senha import digest_texto
from tests.conftest import INSTALACAO_1


def _solicitacao(*, instalacao: str = INSTALACAO_1, agora: int | None = None) -> str:
    instante = int(time.time()) if agora is None else int(agora)
    return recuperacao_contrato.codificar_solicitacao({
        "schema": 1,
        "finalidade": "recuperacao_admin_solicitacao",
        "request_id": "pedido_recuperacao_123456789",
        "desafio": "desafio-secreto-da-instalacao-123456789",
        "installation_id": instalacao,
        "usuario": "referencia-opaca-do-administrador-123456789",
        "versao_app": "1.0.6",
        "iat": instante,
        "exp": instante + 2 * 60 * 60,
    })


def _autenticar(c, main, subject: str, papeis: set[str], *, mfa: bool = True):
    csrf = "csrf-" + subject
    identidade = admin_auth.IdentidadeAdmin(
        subject=subject,
        nome=subject + "@exemplo.test",
        papeis=frozenset(papeis),
        mfa=mfa,
        csrf=csrf,
        expira_em=int(time.time()) + 3600,
    )
    cookie = admin_auth.criar_cookie_sessao(
        identidade, main.estado.oidc.session_secret,
    )
    c.cookies.set(
        admin_auth.COOKIE_SESSAO, cookie, domain="testserver.local", path="/admin",
    )
    return {"X-CSRF-Token": csrf}


def _preparar(c, headers, solicitacao: str):
    return c.post("/admin/recuperacoes/preparar", headers=headers, json={
        "solicitacao": solicitacao,
        "protocolo": "CHAMADO-2026-001",
        "justificativa": "Identidade conferida pelo contato oficial já cadastrado.",
        "metodo_verificacao": "contato_oficial_cadastrado",
        "canal_oficial_confirmado": True,
    })


def test_recuperacao_exige_sessao_corporativa(cliente):
    c, _, _ = cliente
    resposta = c.post("/admin/recuperacoes/validar", json={
        "solicitacao": _solicitacao(),
    })
    assert resposta.status_code == 401


def test_recuperacao_exige_mfa(cliente):
    c, _, main = cliente
    headers = _autenticar(
        c, main, "operador-sem-mfa", {admin_auth.PAPEL_RECUPERACAO_OPERADOR}, mfa=False,
    )
    resposta = c.post("/admin/recuperacoes/validar", headers=headers, json={
        "solicitacao": _solicitacao(),
    })
    assert resposta.status_code == 403
    assert "multifator" in resposta.json()["detail"]


def test_fluxo_com_dupla_aprovacao_emite_token_uma_vez(cliente):
    c, _, main = cliente
    main.estado.repo.autorizar(INSTALACAO_1, "2927408", None)
    solicitacao = _solicitacao()

    headers_operador = _autenticar(
        c, main, "operador-1", {admin_auth.PAPEL_RECUPERACAO_OPERADOR},
    )
    validada = c.post("/admin/recuperacoes/validar", headers=headers_operador, json={
        "solicitacao": solicitacao,
    })
    assert validada.status_code == 200
    assert validada.json()["solicitacao"]["instalacao_conhecida"] is True

    preparada = _preparar(c, headers_operador, solicitacao)
    assert preparada.status_code == 200, preparada.text
    registro = preparada.json()["recuperacao"]
    request_id = registro["request_id"]
    assert registro["estado"] == "preparada"
    assert "desafio" not in preparada.text

    autoaprovacao = c.post(
        f"/admin/recuperacoes/{request_id}/aprovar",
        headers=headers_operador,
        json={"confirmar": True},
    )
    assert autoaprovacao.status_code == 403  # operador não possui papel de aprovador

    headers_aprovador = _autenticar(
        c, main, "aprovador-2", {admin_auth.PAPEL_RECUPERACAO_APROVADOR},
    )
    aprovada = c.post(
        f"/admin/recuperacoes/{request_id}/aprovar",
        headers=headers_aprovador,
        json={"confirmar": True},
    )
    assert aprovada.status_code == 200, aprovada.text
    assert aprovada.json()["recuperacao"]["estado"] == "aprovada"

    emitida = c.post(
        f"/admin/recuperacoes/{request_id}/emitir",
        headers=headers_aprovador,
        json={"solicitacao": solicitacao, "confirmacao": "EMITIR"},
    )
    assert emitida.status_code == 200, emitida.text
    token = emitida.json()["token"]
    assert token.startswith("TFR1.")
    payload, assinatura = recuperacao_contrato.decodificar_token(token)
    publica = base64.b64decode(main.estado.assinador_recuperacao.chave_publica_b64())
    Ed25519PublicKey.from_public_bytes(publica).verify(
        assinatura, recuperacao_contrato.payload_token_para_assinar(payload),
    )
    assert payload["request_id"] == request_id
    assert payload["installation_id"] == INSTALACAO_1
    assert payload["kid"] == "recuperacao-teste-v2"

    repetida = c.post(
        f"/admin/recuperacoes/{request_id}/emitir",
        headers=headers_aprovador,
        json={"solicitacao": solicitacao, "confirmacao": "EMITIR"},
    )
    assert repetida.status_code == 409

    salvo = main.estado.repo.obter_recuperacao(request_id)
    assert salvo["token_digest"] == digest_texto(token)
    assert "token" not in salvo
    assert "desafio" not in salvo
    assert all(token not in str(evento) for evento in main.estado.repo.listar_auditoria_admin(100))


def test_mesma_identidade_nao_pode_preparar_e_aprovar(cliente):
    c, _, main = cliente
    main.estado.repo.autorizar(INSTALACAO_1, "2927408", None)
    solicitacao = _solicitacao()
    papeis = {
        admin_auth.PAPEL_RECUPERACAO_OPERADOR,
        admin_auth.PAPEL_RECUPERACAO_APROVADOR,
    }
    headers = _autenticar(c, main, "operador-duplo", papeis)
    preparada = _preparar(c, headers, solicitacao)
    request_id = preparada.json()["recuperacao"]["request_id"]
    resposta = c.post(
        f"/admin/recuperacoes/{request_id}/aprovar",
        headers=headers,
        json={"confirmar": True},
    )
    assert resposta.status_code == 409
    assert "não pode aprovar" in resposta.json()["detail"]


def test_instalacao_desconhecida_exige_escalonamento(cliente):
    c, _, main = cliente
    headers = _autenticar(
        c, main, "operador-1", {admin_auth.PAPEL_RECUPERACAO_OPERADOR},
    )
    resposta = _preparar(c, headers, _solicitacao(instalacao="instalacao-desconhecida"))
    assert resposta.status_code == 422
    assert "escalonamento" in resposta.json()["detail"].lower()


def test_logout_invalida_cookie_administrativo(cliente):
    c, _, main = cliente
    headers = _autenticar(c, main, "operador-1", {admin_auth.PAPEL_LICENCAS})
    assert c.get("/admin/sessao").json()["autenticado"] is True
    resposta = c.post("/admin/logout", headers=headers)
    assert resposta.status_code == 200
    assert c.get("/admin/sessao").json()["autenticado"] is False


def _b64url(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def test_id_token_oidc_rs256_mapeia_grupo_e_mfa():
    agora = int(time.time())
    privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numeros = privada.public_key().public_numbers()
    jwks = {"keys": [{
        "kty": "RSA",
        "kid": "chave-1",
        "n": _b64url(numeros.n.to_bytes((numeros.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numeros.e.to_bytes((numeros.e.bit_length() + 7) // 8, "big")),
    }]}
    cfg = admin_auth.ConfiguracaoOidc(
        habilitado=True,
        issuer="https://login.exemplo.test/tenant/v2.0",
        authorization_endpoint="https://login.exemplo.test/authorize",
        token_endpoint="https://login.exemplo.test/token",
        jwks_uri="https://login.exemplo.test/jwks",
        client_id="cliente-teste",
        client_secret="segredo",
        redirect_uri="https://servico.test/admin/callback",
        session_secret="s" * 48,
        grupo_recuperacao_operador=("grupo-operador",),
        grupo_recuperacao_aprovador=("grupo-aprovador",),
    )
    cabecalho = _b64url(json.dumps(
        {"alg": "RS256", "kid": "chave-1"}, separators=(",", ":"),
    ).encode())
    payload = _b64url(json.dumps({
        "iss": cfg.issuer,
        "aud": cfg.client_id,
        "sub": "usuario-oidc",
        "preferred_username": "usuario@exemplo.test",
        "nonce": "nonce-1",
        "iat": agora,
        "exp": agora + 600,
        "amr": ["pwd", "mfa"],
        "groups": ["grupo-operador"],
    }, separators=(",", ":")).encode())
    assinatura = privada.sign(
        (cabecalho + "." + payload).encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    claims = admin_auth.validar_id_token(
        cabecalho + "." + payload + "." + _b64url(assinatura),
        "nonce-1",
        cfg,
        agora=agora,
        jwks=jwks,
    )
    identidade = admin_auth.identidade_das_claims(claims, cfg)
    assert identidade.mfa is True
    assert admin_auth.PAPEL_RECUPERACAO_OPERADOR in identidade.papeis
