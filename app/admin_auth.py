# -*- coding: utf-8 -*-
"""Autenticação individual OIDC e sessões administrativas assinadas.

O módulo implementa o subconjunto necessário do Authorization Code + PKCE e
valida ID tokens RS256 com a JWKS configurada. Não aceita tokens sem assinatura,
algoritmos alternativos ou identidade informada pelo navegador.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

COOKIE_SESSAO = "techfisco_admin_sessao"
COOKIE_LOGIN = "techfisco_admin_oidc"
TTL_LOGIN = 10 * 60
TTL_SESSAO = 8 * 60 * 60
TOLERANCIA_RELOGIO = 120

PAPEL_LICENCAS = "licencas_operador"
PAPEL_RECUPERACAO_OPERADOR = "recuperacao_operador"
PAPEL_RECUPERACAO_APROVADOR = "recuperacao_aprovador"
PAPEL_AUDITORIA = "auditoria_leitura"
PAPEIS = frozenset({
    PAPEL_LICENCAS,
    PAPEL_RECUPERACAO_OPERADOR,
    PAPEL_RECUPERACAO_APROVADOR,
    PAPEL_AUDITORIA,
})


class ErroAutenticacao(ValueError):
    """Credencial, callback ou configuração OIDC inválida."""


@dataclass(frozen=True)
class ConfiguracaoOidc:
    habilitado: bool = False
    issuer: str = ""
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    jwks_uri: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    session_secret: str = ""
    grupo_licencas: tuple[str, ...] = ()
    grupo_recuperacao_operador: tuple[str, ...] = ()
    grupo_recuperacao_aprovador: tuple[str, ...] = ()
    grupo_auditoria: tuple[str, ...] = ()
    exigir_mfa: bool = True

    @property
    def configurado(self) -> bool:
        return bool(
            self.habilitado
            and self.issuer
            and self.authorization_endpoint
            and self.token_endpoint
            and self.jwks_uri
            and self.client_id
            and self.client_secret
            and self.redirect_uri
            and len(self.session_secret) >= 32
            and self.grupo_recuperacao_operador
            and self.grupo_recuperacao_aprovador
        )


@dataclass(frozen=True)
class IdentidadeAdmin:
    subject: str
    nome: str
    papeis: frozenset[str]
    mfa: bool
    csrf: str
    expira_em: int

    def possui(self, papel: str) -> bool:
        return papel in self.papeis


def _b64url_codificar(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


def _b64url_decodificar(texto: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))
    except Exception as exc:
        raise ErroAutenticacao("codificação inválida") from exc


def _json_objeto(bruto: bytes) -> dict[str, Any]:
    try:
        valor = json.loads(bruto.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ErroAutenticacao("JSON de autenticação inválido") from exc
    if not isinstance(valor, dict):
        raise ErroAutenticacao("objeto de autenticação inválido")
    return valor


def _assinar_cookie(payload: dict[str, Any], segredo: str) -> str:
    bruto = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    corpo = _b64url_codificar(bruto)
    assinatura = hmac.new(segredo.encode("utf-8"), corpo.encode("ascii"), hashlib.sha256).digest()
    return corpo + "." + _b64url_codificar(assinatura)


def _ler_cookie(valor: str, segredo: str, *, agora: int | None = None) -> dict[str, Any]:
    instante = int(time.time()) if agora is None else int(agora)
    try:
        corpo, assinatura_texto = (valor or "").split(".")
        recebida = _b64url_decodificar(assinatura_texto)
    except (ValueError, ErroAutenticacao) as exc:
        raise ErroAutenticacao("sessão inválida") from exc
    esperada = hmac.new(
        segredo.encode("utf-8"), corpo.encode("ascii"), hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(recebida, esperada):
        raise ErroAutenticacao("sessão inválida")
    payload = _json_objeto(_b64url_decodificar(corpo))
    try:
        expira = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ErroAutenticacao("sessão inválida") from exc
    if expira < instante:
        raise ErroAutenticacao("sessão expirada")
    return payload


def criar_inicio_login(
    cfg: ConfiguracaoOidc, *, agora: int | None = None,
) -> tuple[str, str]:
    if not cfg.configurado:
        raise ErroAutenticacao("OIDC não configurado")
    instante = int(time.time()) if agora is None else int(agora)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url_codificar(hashlib.sha256(verifier.encode("ascii")).digest())
    transacao = _assinar_cookie({
        "tipo": "login",
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "exp": instante + TTL_LOGIN,
    }, cfg.session_secret)
    consulta = urllib.parse.urlencode({
        "client_id": cfg.client_id,
        "response_type": "code",
        "redirect_uri": cfg.redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return cfg.authorization_endpoint + ("&" if "?" in cfg.authorization_endpoint else "?") + consulta, transacao


def validar_inicio_login(
    cookie: str, state: str, cfg: ConfiguracaoOidc, *, agora: int | None = None,
) -> dict[str, Any]:
    transacao = _ler_cookie(cookie, cfg.session_secret, agora=agora)
    if transacao.get("tipo") != "login" or not hmac.compare_digest(
        str(transacao.get("state") or ""), str(state or ""),
    ):
        raise ErroAutenticacao("estado OIDC inválido")
    return transacao


def trocar_codigo(
    codigo: str, verifier: str, cfg: ConfiguracaoOidc, *, timeout: int = 10,
) -> str:
    corpo = urllib.parse.urlencode({
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "grant_type": "authorization_code",
        "code": codigo,
        "redirect_uri": cfg.redirect_uri,
        "code_verifier": verifier,
    }).encode("ascii")
    requisicao = urllib.request.Request(
        cfg.token_endpoint,
        data=corpo,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            dados = _json_objeto(resposta.read(256 * 1024))
    except Exception as exc:
        raise ErroAutenticacao("não foi possível concluir a autenticação") from exc
    token = dados.get("id_token")
    if not isinstance(token, str) or not token:
        raise ErroAutenticacao("provedor não devolveu um ID token")
    return token


_jwks_lock = threading.Lock()
_jwks_cache: dict[str, tuple[int, dict[str, Any]]] = {}


def _carregar_jwks(uri: str, *, agora: int, timeout: int = 10) -> dict[str, Any]:
    with _jwks_lock:
        cache = _jwks_cache.get(uri)
        if cache and cache[0] >= agora:
            return cache[1]
    try:
        requisicao = urllib.request.Request(uri, headers={"Accept": "application/json"})
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            dados = _json_objeto(resposta.read(512 * 1024))
    except Exception as exc:
        raise ErroAutenticacao("não foi possível obter as chaves do provedor") from exc
    if not isinstance(dados.get("keys"), list):
        raise ErroAutenticacao("JWKS inválida")
    with _jwks_lock:
        _jwks_cache[uri] = (agora + 60 * 60, dados)
    return dados


def _chave_rsa(jwk: dict[str, Any]):
    if jwk.get("kty") != "RSA" or not jwk.get("n") or not jwk.get("e"):
        raise ErroAutenticacao("chave OIDC incompatível")
    n = int.from_bytes(_b64url_decodificar(str(jwk["n"])), "big")
    e = int.from_bytes(_b64url_decodificar(str(jwk["e"])), "big")
    return rsa.RSAPublicNumbers(e=e, n=n).public_key()


def _lista_claim(valor: Any) -> set[str]:
    if isinstance(valor, str):
        return {valor}
    if isinstance(valor, list):
        return {str(item) for item in valor if isinstance(item, str)}
    return set()


def _papeis(claims: dict[str, Any], cfg: ConfiguracaoOidc) -> frozenset[str]:
    grupos = _lista_claim(claims.get("groups"))
    papeis_claim = _lista_claim(claims.get("roles"))
    saida = {papel for papel in PAPEIS if papel in papeis_claim}
    mapeamento: tuple[tuple[str, Iterable[str]], ...] = (
        (PAPEL_LICENCAS, cfg.grupo_licencas),
        (PAPEL_RECUPERACAO_OPERADOR, cfg.grupo_recuperacao_operador),
        (PAPEL_RECUPERACAO_APROVADOR, cfg.grupo_recuperacao_aprovador),
        (PAPEL_AUDITORIA, cfg.grupo_auditoria),
    )
    for papel, permitidos in mapeamento:
        if grupos.intersection(permitidos):
            saida.add(papel)
    return frozenset(saida)


def validar_id_token(
    token: str,
    nonce: str,
    cfg: ConfiguracaoOidc,
    *,
    agora: int | None = None,
    jwks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instante = int(time.time()) if agora is None else int(agora)
    try:
        cabecalho_texto, payload_texto, assinatura_texto = token.split(".")
        cabecalho = _json_objeto(_b64url_decodificar(cabecalho_texto))
        claims = _json_objeto(_b64url_decodificar(payload_texto))
        assinatura = _b64url_decodificar(assinatura_texto)
    except (ValueError, ErroAutenticacao) as exc:
        raise ErroAutenticacao("ID token inválido") from exc
    if cabecalho.get("alg") != "RS256" or not isinstance(cabecalho.get("kid"), str):
        raise ErroAutenticacao("algoritmo OIDC não permitido")
    conjunto = jwks or _carregar_jwks(cfg.jwks_uri, agora=instante)
    chave_jwk = next(
        (item for item in conjunto.get("keys", []) if item.get("kid") == cabecalho["kid"]),
        None,
    )
    if not isinstance(chave_jwk, dict):
        raise ErroAutenticacao("chave OIDC desconhecida")
    try:
        _chave_rsa(chave_jwk).verify(
            assinatura,
            (cabecalho_texto + "." + payload_texto).encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise ErroAutenticacao("assinatura OIDC inválida") from exc
    try:
        exp = int(claims["exp"])
        iat = int(claims["iat"])
        nbf = int(claims.get("nbf", iat))
    except (KeyError, TypeError, ValueError) as exc:
        raise ErroAutenticacao("prazo do ID token inválido") from exc
    audiencias = _lista_claim(claims.get("aud"))
    valido = (
        hmac.compare_digest(str(claims.get("iss") or "").rstrip("/"), cfg.issuer.rstrip("/"))
        and cfg.client_id in audiencias
        and exp >= instante - TOLERANCIA_RELOGIO
        and iat <= instante + TOLERANCIA_RELOGIO
        and nbf <= instante + TOLERANCIA_RELOGIO
        and str(claims.get("sub") or "")
        and hmac.compare_digest(str(claims.get("nonce") or ""), nonce)
        and (
            len(audiencias) <= 1
            or hmac.compare_digest(str(claims.get("azp") or ""), cfg.client_id)
        )
    )
    if not valido:
        raise ErroAutenticacao("claims do ID token inválidas")
    return claims


def identidade_das_claims(
    claims: dict[str, Any], cfg: ConfiguracaoOidc,
) -> IdentidadeAdmin:
    amr = {item.lower() for item in _lista_claim(claims.get("amr"))}
    acrs = {item.lower() for item in _lista_claim(claims.get("acrs"))}
    mfa = "mfa" in amr or "c1" in acrs
    if cfg.exigir_mfa and not mfa:
        raise ErroAutenticacao("autenticação multifator obrigatória")
    papeis = _papeis(claims, cfg)
    if not papeis:
        raise ErroAutenticacao("usuário sem perfil administrativo")
    nome = str(
        claims.get("preferred_username") or claims.get("name") or claims.get("sub") or ""
    )[:200]
    return IdentidadeAdmin(
        subject=str(claims["sub"]),
        nome=nome,
        papeis=papeis,
        mfa=mfa,
        csrf=secrets.token_urlsafe(24),
        expira_em=min(int(claims["exp"]), int(time.time()) + TTL_SESSAO),
    )


def criar_cookie_sessao(identidade: IdentidadeAdmin, segredo: str) -> str:
    return _assinar_cookie({
        "tipo": "sessao",
        "sub": identidade.subject,
        "nome": identidade.nome,
        "papeis": sorted(identidade.papeis),
        "mfa": identidade.mfa,
        "csrf": identidade.csrf,
        "exp": identidade.expira_em,
    }, segredo)


def ler_cookie_sessao(
    valor: str, segredo: str, *, agora: int | None = None,
) -> IdentidadeAdmin:
    payload = _ler_cookie(valor, segredo, agora=agora)
    papeis = frozenset(str(p) for p in payload.get("papeis", []) if str(p) in PAPEIS)
    if (
        payload.get("tipo") != "sessao"
        or not str(payload.get("sub") or "")
        or not papeis
        or not str(payload.get("csrf") or "")
    ):
        raise ErroAutenticacao("sessão inválida")
    return IdentidadeAdmin(
        subject=str(payload["sub"]),
        nome=str(payload.get("nome") or payload["sub"]),
        papeis=papeis,
        mfa=bool(payload.get("mfa")),
        csrf=str(payload["csrf"]),
        expira_em=int(payload["exp"]),
    )
