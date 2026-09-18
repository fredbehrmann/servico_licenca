#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Homologação ponta a ponta do licenciamento TechFisco.

Este programa cria um banco PostgreSQL descartável, gera uma chave Ed25519 e um
token exclusivos da execução, sobe o serviço de licenças e o SICOF em portas
locais e percorre o roteiro da Etapa 12. Nenhum dado ou segredo de produção é
usado. O banco criado por este programa é removido ao final.

Uso:

    TEST_DATABASE_URL=postgresql://usuario:senha@127.0.0.1:5432/postgres \
      python scripts/homologar_fluxo_local.py \
      --evidencias /tmp/techfisco-etapa12

O usuário da URL deve poder criar e excluir bancos. Somente bancos com o
prefixo ``techfisco_homolog_`` criados pela própria execução são removidos.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


SERVICO_DIR = Path(__file__).resolve().parents[1]
SICOF_DIR = SERVICO_DIR.parent / "sicof"
SICOF_APP_DIR = SICOF_DIR / "SICOF_NEXT"
IBGE = "2927408"
KEY_ID = "homolog-etapa12-v1"
OPERADOR = "homologacao-etapa12"
REF_RETORNO_SERVICO = "a906b9d"
REF_RETORNO_SICOF = "e9277b9"
SENHA_ADMIN = "Adm-Homolog-2026!Seguro"
SENHA_AUDITOR = "Aud-Homolog-2026!Seguro"


class HomologacaoErro(RuntimeError):
    """Um resultado observado divergiu do roteiro de homologação."""


@dataclass
class Evidencia:
    cenario: str
    nome: str
    esperado: str
    observado: str
    resultado: str = "aprovado"


class Processo:
    def __init__(
        self, nome: str, cwd: Path, ambiente: dict[str, str], porta: int, log: Path,
    ) -> None:
        self.nome = nome
        self.cwd = cwd
        self.ambiente = ambiente
        self.porta = porta
        self.log = log
        self._arquivo = None
        self.processo: subprocess.Popen | None = None

    def iniciar(self) -> None:
        if self.processo is not None:
            raise HomologacaoErro(f"{self.nome} já está em execução")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self._arquivo = self.log.open("a", encoding="utf-8")
        self.processo = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app" if self.nome.startswith("servico") else "server:app",
                "--host", "127.0.0.1", "--port", str(self.porta), "--workers", "1",
            ],
            cwd=self.cwd,
            env=self.ambiente,
            stdout=self._arquivo,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def parar(self) -> None:
        processo = self.processo
        if processo is None:
            return
        if processo.poll() is None:
            processo.send_signal(signal.SIGTERM)
            try:
                processo.wait(timeout=10)
            except subprocess.TimeoutExpired:
                processo.kill()
                processo.wait(timeout=5)
        self.processo = None
        if self._arquivo is not None:
            self._arquivo.close()
            self._arquivo = None

    def confirmar_vivo(self) -> None:
        if self.processo is None or self.processo.poll() is not None:
            detalhe = ""
            if self.log.is_file():
                detalhe = self.log.read_text(encoding="utf-8", errors="replace")[-3000:]
            raise HomologacaoErro(f"{self.nome} encerrou antes do esperado\n{detalhe}")


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _url_sqlalchemy(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _git_commit(diretorio: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=diretorio,
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _extrair_ref(repositorio: Path, ref: str, destino: Path) -> None:
    proc = subprocess.run(
        ["git", "archive", "--format=tar", ref], cwd=repositorio,
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise HomologacaoErro(
            f"não foi possível extrair {ref} de {repositorio}: "
            f"{proc.stderr.decode('utf-8', errors='replace')}"
        )
    destino.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as arquivo:
        arquivo.extractall(destino, filter="data")


def _esperar_http(
    processo: Processo, url: str, status: Iterable[int] = (200,), timeout: float = 45,
) -> httpx.Response:
    limite = time.monotonic() + timeout
    ultimo: Exception | None = None
    esperados = set(status)
    while time.monotonic() < limite:
        processo.confirmar_vivo()
        try:
            resposta = httpx.get(url, timeout=2)
            if resposta.status_code in esperados:
                return resposta
            ultimo = HomologacaoErro(f"HTTP {resposta.status_code}: {resposta.text[:500]}")
        except Exception as exc:
            ultimo = exc
        time.sleep(0.25)
    raise HomologacaoErro(f"tempo esgotado aguardando {url}: {ultimo}")


def _exigir(condicao: bool, mensagem: str) -> None:
    if not condicao:
        raise HomologacaoErro(mensagem)


def _json_ok(resposta: httpx.Response, *, status: int = 200) -> dict[str, Any]:
    _exigir(
        resposta.status_code == status,
        f"esperado HTTP {status}, recebido {resposta.status_code}: {resposta.text[:1000]}",
    )
    corpo = resposta.json()
    _exigir(isinstance(corpo, dict), "resposta não é um objeto JSON")
    return corpo


def _admin_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Admin-Operador": OPERADOR,
    }


def _api_sicof(cliente: httpx.Client, metodo: str, *args: Any) -> dict[str, Any]:
    resposta = cliente.post(f"/api/call/{metodo}", json={"args": list(args)})
    return _json_ok(resposta)


def _login_sicof(base_url: str) -> httpx.Client:
    cliente = httpx.Client(base_url=base_url, follow_redirects=False, timeout=15)
    resposta = cliente.post(
        "/login", data={"usuario": "admin.homolog@x.gov.br", "senha": SENHA_ADMIN},
    )
    _exigir(
        resposta.status_code == 303 and resposta.headers.get("location") == "/",
        f"login do administrador falhou: {resposta.status_code} {resposta.text[:500]}",
    )
    return cliente


def _consultar_servico(base_url: str, instalacao_id: str, ibge: str, nonce: str) -> dict[str, Any]:
    resposta = httpx.post(
        f"{base_url}/v1/consulta",
        json={
            "instalacao_id": instalacao_id,
            "codigo_ibge": ibge,
            "versao_app": "1.0.0",
            "nonce": nonce,
        },
        timeout=15,
    )
    return _json_ok(resposta)


def _atualizar_cache(cliente: httpx.Client, *, libera: bool) -> httpx.Response:
    resposta = cliente.post(
        "/ativacao",
        data={"codigo_ibge": IBGE, "nome_municipio": "Município Fictício Etapa 12"},
    )
    if libera:
        _exigir(
            resposta.status_code == 303 and resposta.headers.get("location") == "/",
            f"licença deveria liberar o SICOF: {resposta.status_code} {resposta.text[:500]}",
        )
    else:
        _exigir(
            resposta.status_code == 202,
            f"licença deveria manter o SICOF restrito: {resposta.status_code} {resposta.text[:500]}",
        )
    return resposta


def _avaliar_cache_no_futuro(
    dados_dir: Path, publica_b64: str,
) -> dict[str, Any]:
    programa = r"""
import json
from datetime import datetime, timedelta
from app import licenca_cache, licenca_chaves, licenca_cliente
from app.licenca_modelos import RequisicaoLicenca

cache = licenca_cache.ler()
req = RequisicaoLicenca(
    cache["instalacao_id"], cache["codigo_ibge"], "1.0.0", "nonce-futuro"
)
agora = datetime.fromisoformat(cache["offline_ate"]) + timedelta(seconds=1)
resposta, persistir = licenca_cliente.avaliar(
    None, licenca_cliente.ErroTransporte("serviço indisponível"), cache,
    req, agora, licenca_chaves.chaves_publicas(),
)
print(json.dumps({"status": resposta["status"], "persistir": persistir}))
"""
    ambiente = os.environ.copy()
    ambiente.update({
        "PYTHONPATH": str(SICOF_APP_DIR),
        "SICOF_DATA_DIR": str(dados_dir),
        "SICOF_AMBIENTE": "homologacao",
        "SICOF_LICENCA_GATE": "1",
        "SICOF_LICENCA_CHAVES_EXTRA": f"{KEY_ID}:{publica_b64}",
    })
    proc = subprocess.run(
        [sys.executable, "-c", programa], cwd=SICOF_DIR, env=ambiente,
        capture_output=True, text=True, timeout=20, check=False,
    )
    _exigir(proc.returncode == 0, f"avaliação futura falhou: {proc.stderr}")
    return json.loads(proc.stdout)


def _sanitizar_log(origem: Path, destino: Path, segredos: Iterable[str]) -> None:
    texto_log = origem.read_text(encoding="utf-8", errors="replace") if origem.is_file() else ""
    for segredo in segredos:
        if segredo:
            texto_log = texto_log.replace(segredo, "<redigido>")
    texto_log = re.sub(
        r"postgres(?:ql)?(?:\+psycopg)?://[^\s/@:]+:[^\s/@]+@",
        "postgresql://<credenciais>@",
        texto_log,
    )
    destino.write_text(texto_log, encoding="utf-8")


def executar(args: argparse.Namespace) -> int:
    if not SICOF_APP_DIR.is_dir():
        raise HomologacaoErro(f"repositório SICOF não encontrado em {SICOF_DIR}")
    base_banco = args.database_admin_url or os.environ.get("TEST_DATABASE_URL", "").strip()
    if not base_banco:
        raise HomologacaoErro("informe TEST_DATABASE_URL ou --database-admin-url")

    evidencias_dir = Path(args.evidencias).resolve()
    evidencias_dir.mkdir(parents=True, exist_ok=True)
    temporario = Path(tempfile.mkdtemp(prefix="techfisco-etapa12-"))
    dados_sicof = temporario / "dados-sicof"
    dados_sicof.mkdir(parents=True)
    logs_brutos = temporario / "logs"
    logs_brutos.mkdir()

    evidencias: list[Evidencia] = []
    falha: str | None = None
    processo_servico: Processo | None = None
    processo_sicof: Processo | None = None
    admin_engine = None
    banco_nome = "techfisco_homolog_" + uuid.uuid4().hex
    banco_url = ""
    token_admin = secrets.token_urlsafe(40)
    auth_secret = secrets.token_urlsafe(48)
    privada = Ed25519PrivateKey.generate()
    privada_pem = privada.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    publica = privada.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    publica_b64 = base64.b64encode(publica).decode("ascii")
    porta_servico = _porta_livre()
    porta_sicof = _porta_livre()
    servico_url = f"http://127.0.0.1:{porta_servico}"
    sicof_url = f"http://127.0.0.1:{porta_sicof}"

    def registrar(cenario: str, nome: str, esperado: str, observado: str) -> None:
        evidencias.append(Evidencia(cenario, nome, esperado, observado))

    def ambiente_servico(cwd: Path) -> dict[str, str]:
        ambiente = os.environ.copy()
        ambiente.update({
            "PYTHONPATH": str(cwd),
            "DATABASE_URL": banco_url,
            "LICENCA_PRIVADA_PEM": privada_pem,
            "LICENCA_PUBLICA_B64_ESPERADA": publica_b64,
            "LICENCA_KEY_ID": KEY_ID,
            "LICENCA_AMBIENTE": "homologacao",
            "LICENCA_REPLICAS": "1",
            "LICENCA_RATE_MAX": "1000",
            "LICENCA_RATE_JANELA_S": "3600",
            "ADMIN_TOKEN": token_admin,
        })
        return ambiente

    def ambiente_sicof(cwd: Path) -> dict[str, str]:
        ambiente = os.environ.copy()
        ambiente.update({
            "PYTHONPATH": str(cwd),
            "SICOF_DATA_DIR": str(dados_sicof),
            "SICOF_AMBIENTE": "homologacao",
            "SICOF_LICENCA_GATE": "1",
            "SICOF_LICENCA_URL": f"{servico_url}/v1/consulta",
            "SICOF_LICENCA_CHAVES_EXTRA": f"{KEY_ID}:{publica_b64}",
            "SICOF_SESSAO": "memoria",
            "SICOF_AUTH_SECRET": auth_secret,
            "WEB_CONCURRENCY": "1",
        })
        ambiente.pop("SICOF_PERMITIR_GATE_DESLIGADO", None)
        ambiente.pop("SICOF_SENHA", None)
        return ambiente

    def iniciar_servico(cwd: Path = SERVICO_DIR, nome: str = "servico-atual") -> Processo:
        processo = Processo(nome, cwd, ambiente_servico(cwd), porta_servico,
                           logs_brutos / f"{nome}.log")
        processo.iniciar()
        _esperar_http(processo, f"{servico_url}/ready" if cwd == SERVICO_DIR else f"{servico_url}/health")
        return processo

    def iniciar_sicof(cwd: Path = SICOF_APP_DIR, nome: str = "sicof-atual") -> Processo:
        processo = Processo(nome, cwd, ambiente_sicof(cwd), porta_sicof,
                           logs_brutos / f"{nome}.log")
        processo.iniciar()
        _esperar_http(processo, f"{sicof_url}/health")
        return processo

    try:
        admin_url = _url_sqlalchemy(base_banco)
        admin_engine = create_engine(admin_url, pool_pre_ping=True)
        _exigir(re.fullmatch(r"techfisco_homolog_[a-f0-9]{32}", banco_nome) is not None,
                "nome de banco descartável inválido")
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as con:
            con.execute(text(f'CREATE DATABASE "{banco_nome}"'))
        banco_url = make_url(admin_url).set(database=banco_nome).render_as_string(
            hide_password=False
        )

        processo_servico = iniciar_servico()
        pronto = _json_ok(httpx.get(f"{servico_url}/ready", timeout=10))
        _exigir(pronto.get("ok") is True, f"serviço não ficou pronto: {pronto}")
        registrar(
            "estrutura", "ambiente isolado",
            "PostgreSQL, chave, token e URLs exclusivos da homologação local",
            "serviço pronto em banco descartável e chave Ed25519 gerada para esta execução",
        )

        processo_sicof = iniciar_sicof()
        sicof = httpx.Client(base_url=sicof_url, follow_redirects=False, timeout=20)
        _exigir(sicof.get("/primeiro-acesso").status_code == 200,
                "tela de primeiro acesso não abriu")
        criada = sicof.post("/primeiro-acesso", data={
            "nome": "Administrador Homologação",
            "usuario": "admin.homolog@x.gov.br",
            "senha": SENHA_ADMIN,
            "confirmar": SENHA_ADMIN,
            "matricula": "HOM-001",
        })
        _exigir(criada.status_code == 303 and criada.headers.get("location") == "/ativacao",
                f"primeiro administrador não seguiu para ativação: {criada.status_code}")
        pendente = sicof.post("/ativacao", data={
            "codigo_ibge": IBGE,
            "nome_municipio": "Município Fictício Etapa 12",
        })
        _exigir(pendente.status_code == 202, "instalação não autorizada deveria ficar restrita")
        pagina = sicof.get("/ativacao")
        encontrado = re.search(r"[0-9a-f]{8}-[0-9a-f-]{27,}", pagina.text, re.I)
        _exigir(encontrado is not None, "instalacao_id não apareceu na tela de ativação")
        instalacao_id = encontrado.group(0)

        headers = _admin_headers(token_admin)
        agora = datetime.now(timezone.utc)
        inicio = agora - timedelta(days=30)
        fim = agora + timedelta(days=365)
        criada_licenca = _json_ok(httpx.post(
            f"{servico_url}/admin/licencas", headers=headers, json={
                "codigo_ibge": IBGE,
                "status": "ativa",
                "inicio_em": inicio.isoformat(),
                "expira_em": fim.isoformat(),
                "max_auditores": 5,
                "max_instalacoes_ativas": 1,
                "dias_offline": 7,
                "versao_minima": "1.0.0",
            }, timeout=15,
        ))
        licenca_id = criada_licenca["licenca"]["licenca_id"]
        _json_ok(httpx.post(
            f"{servico_url}/admin/licencas/{licenca_id}/instalacoes",
            headers=headers,
            json={"instalacao_id": instalacao_id, "status": "ativa"},
            timeout=15,
        ))
        _atualizar_cache(sicof, libera=True)
        _exigir(sicof.get("/").status_code == 200, "SICOF não abriu após autorização")
        registrar(
            "01", "primeira ativação",
            "administrador entra em modo restrito e a associação administrativa libera o app",
            "modo restrito retornou 202; associação pela API liberou a aplicação",
        )

        correta = _consultar_servico(servico_url, instalacao_id, IBGE, "vinculo-correto")
        errada = _consultar_servico(servico_url, instalacao_id, "3550308", "vinculo-errado")
        _exigir(correta["status"] == "ativa", "vínculo municipal correto foi recusado")
        _exigir(errada["status"] == "instalacao_nao_autorizada",
                "instalação foi autorizada para município diferente")
        registrar(
            "02", "vínculo municipal",
            "IBGE correto autoriza e IBGE adulterado é recusado",
            "correto=ativa; adulterado=instalacao_nao_autorizada",
        )

        auditores: list[str] = []
        for indice in range(5):
            resposta = _api_sicof(
                sicof, "usuario_criar", f"aud{indice}@x.gov.br", SENHA_AUDITOR,
                "auditor", f"Auditor {indice}", f"A-{indice}",
            )
            _exigir(resposta.get("ok") is True, f"auditor {indice} não foi criado: {resposta}")
            auditores.append(resposta["usuario"]["usuario_id"])
        sexto = _api_sicof(
            sicof, "usuario_criar", "sexto@x.gov.br", SENHA_AUDITOR,
            "auditor", "Sexto Auditor", "A-6",
        )
        _exigir(sexto.get("ok") is False and "assentos" in sexto.get("erro", ""),
                f"sexto auditor não foi bloqueado pelo limite: {sexto}")
        _exigir(_api_sicof(sicof, "usuario_desativar", auditores[0]).get("ok") is True,
                "não foi possível desativar auditor")
        substituto = _api_sicof(
            sicof, "usuario_criar", "sexto@x.gov.br", SENHA_AUDITOR,
            "auditor", "Auditor Substituto", "A-6",
        )
        _exigir(substituto.get("ok") is True,
                "desativar um auditor não liberou o assento")
        auditores.append(substituto["usuario"]["usuario_id"])
        registrar(
            "03", "assentos",
            "cinco auditores entram, o sexto é recusado e uma desativação libera a vaga",
            "limite aplicado e vaga reutilizada sem excluir o histórico",
        )

        admin2 = _api_sicof(
            sicof, "usuario_criar", "admin2@x.gov.br", SENHA_ADMIN,
            "administrador", "Administrador Dois", "HOM-002",
        )
        _exigir(admin2.get("ok") is True, "administrador deveria ser criado sem consumir assento")
        admin2_id = admin2["usuario"]["usuario_id"]
        promocao = _api_sicof(sicof, "usuario_alterar_papel", admin2_id, "auditor")
        _exigir(promocao.get("ok") is False, "administrador virou auditor com limite cheio")
        _exigir(_api_sicof(sicof, "usuario_desativar", admin2_id).get("ok") is True,
                "segundo administrador não pôde ser desativado")
        listagem = _api_sicof(sicof, "usuario_listar")
        admin1 = next(u for u in listagem["usuarios"] if u.get("email") == "admin.homolog@x.gov.br")
        ultimo = _api_sicof(sicof, "usuario_desativar", admin1["usuario_id"])
        _exigir(ultimo.get("ok") is False and "administrador" in ultimo.get("erro", "").lower(),
                "proteção do último administrador não atuou")
        registrar(
            "04", "papéis",
            "administrador não ocupa vaga; mudança para auditor respeita o teto; último admin é protegido",
            "os três comportamentos foram confirmados pela API real do SICOF",
        )

        ampliada = _json_ok(httpx.patch(
            f"{servico_url}/admin/licencas/{licenca_id}", headers=headers,
            json={"max_auditores": 8}, timeout=15,
        ))
        _exigir(ampliada["licenca"]["max_auditores"] == 8, "ampliação para oito falhou")
        _atualizar_cache(sicof, libera=True)
        for indice in range(3):
            extra = _api_sicof(
                sicof, "usuario_criar", f"extra{indice}@x.gov.br", SENHA_AUDITOR,
                "auditor", f"Auditor Extra {indice}", f"E-{indice}",
            )
            _exigir(extra.get("ok") is True, f"auditor extra {indice} não foi criado")
        reduzida = _json_ok(httpx.patch(
            f"{servico_url}/admin/licencas/{licenca_id}", headers=headers,
            json={"max_auditores": 5}, timeout=15,
        ))
        _exigir(reduzida["licenca"]["max_auditores"] == 5, "redução para cinco falhou")
        _atualizar_cache(sicof, libera=True)
        apos_reducao = _api_sicof(sicof, "usuario_listar")
        _exigir(apos_reducao["assentos"]["auditores_ativos"] == 8,
                "a redução desativou auditores automaticamente")
        _exigir(apos_reducao["assentos"]["excedente"] == 3,
                "excedência esperada não foi calculada")
        novo_excedente = _api_sicof(
            sicof, "usuario_criar", "excedente@x.gov.br", SENHA_AUDITOR,
            "auditor", "Auditor Excedente", "E-9",
        )
        _exigir(novo_excedente.get("ok") is False,
                "novo auditor foi criado durante excedência")
        registrar(
            "05", "redução contratual",
            "reduzir de oito para cinco preserva contas e bloqueia crescimento",
            "oito permaneceram ativos, excedente=3 e novo cadastro foi recusado",
        )

        expiracao = datetime.now(timezone.utc) - timedelta(minutes=1)
        _json_ok(httpx.patch(
            f"{servico_url}/admin/licencas/{licenca_id}", headers=headers,
            json={
                "expira_em": expiracao.isoformat(),
                "confirmar_encurtamento_vigencia": True,
            }, timeout=15,
        ))
        _atualizar_cache(sicof, libera=False)
        bloqueada = sicof.get("/")
        _exigir(bloqueada.status_code == 303 and bloqueada.headers.get("location") == "/licenca",
                "licença vencida não bloqueou a aplicação")
        nova_expiracao = datetime.now(timezone.utc) + timedelta(days=365)
        renovada = _json_ok(httpx.patch(
            f"{servico_url}/admin/licencas/{licenca_id}", headers=headers,
            json={"expira_em": nova_expiracao.isoformat(), "status": "ativa"}, timeout=15,
        ))
        _atualizar_cache(sicof, libera=True)
        visivel = _api_sicof(sicof, "usuario_listar")["assentos"]["expira_em"]
        _exigir(visivel == renovada["licenca"]["expira_em"],
                "SICOF não exibiu a data renovada do contrato")
        _json_ok(httpx.patch(
            f"{servico_url}/admin/licencas/{licenca_id}", headers=headers,
            json={"status": "suspensa"}, timeout=15,
        ))
        _atualizar_cache(sicof, libera=False)
        _json_ok(httpx.patch(
            f"{servico_url}/admin/licencas/{licenca_id}", headers=headers,
            json={"status": "ativa"}, timeout=15,
        ))
        _atualizar_cache(sicof, libera=True)
        registrar(
            "06", "expiração, renovação e suspensão",
            "vencimento e suspensão bloqueiam; renovação/reativação restauram e mostram nova data",
            "bloqueios e liberações ocorreram sem edição direta do banco",
        )

        _json_ok(httpx.post(
            f"{servico_url}/admin/licencas/{licenca_id}/instalacoes",
            headers=headers,
            json={"instalacao_id": instalacao_id, "status": "revogada"}, timeout=15,
        ))
        _atualizar_cache(sicof, libera=False)
        processo_servico.parar()
        sem_servico = sicof.get("/")
        _exigir(sem_servico.status_code == 303 and sem_servico.headers.get("location") == "/licenca",
                "cache ativo antigo ressuscitou instalação revogada")
        processo_servico = iniciar_servico()
        _json_ok(httpx.post(
            f"{servico_url}/admin/licencas/{licenca_id}/instalacoes",
            headers=headers,
            json={"instalacao_id": instalacao_id, "status": "ativa"}, timeout=15,
        ))
        _atualizar_cache(sicof, libera=True)
        _json_ok(httpx.post(
            f"{servico_url}/admin/revogar-municipio", headers=headers,
            json={"codigo_ibge": IBGE}, timeout=15,
        ))
        _atualizar_cache(sicof, libera=False)
        _json_ok(httpx.post(
            f"{servico_url}/admin/reativar-municipio", headers=headers,
            json={"codigo_ibge": IBGE}, timeout=15,
        ))
        _atualizar_cache(sicof, libera=True)
        registrar(
            "07", "revogação",
            "revogar instalação ou município bloqueia e cache anterior não reabre o acesso",
            "ambas as revogações bloquearam; cache revogado permaneceu fechado sem serviço",
        )

        processo_servico.parar()
        processo_sicof.parar()
        processo_sicof = iniciar_sicof()
        sicof.close()
        sicof = _login_sicof(sicof_url)
        _exigir(sicof.get("/").status_code == 200,
                "cache válido não sustentou o uso dentro da tolerância offline")
        futuro = _avaliar_cache_no_futuro(dados_sicof, publica_b64)
        _exigir(futuro == {"status": "erro_temporario", "persistir": False},
                f"cache permaneceu válido além de offline_ate: {futuro}")
        registrar(
            "08", "funcionamento sem internet",
            "cache assinado libera dentro da tolerância e bloqueia depois de offline_ate",
            "reinício sem serviço abriu pelo cache; relógio simulado após o prazo recusou o cache",
        )

        processo_servico = iniciar_servico()
        persistida = _consultar_servico(servico_url, instalacao_id, IBGE, "apos-reinicio")
        _exigir(persistida["status"] == "ativa" and persistida["max_usuarios"] == 5,
                f"autorização não persistiu no PostgreSQL: {persistida}")
        _atualizar_cache(sicof, libera=True)
        registrar(
            "09", "reinício do serviço",
            "contrato e instalação continuam autorizados após novo processo",
            "consulta após reinício retornou ativa com limite de cinco auditores",
        )

        processo_servico.parar()
        rollback_servico = temporario / "rollback-servico"
        _extrair_ref(SERVICO_DIR, REF_RETORNO_SERVICO, rollback_servico)
        processo_servico = iniciar_servico(rollback_servico, "servico-retorno")
        servico_antigo = _consultar_servico(servico_url, instalacao_id, IBGE, "rollback-servico")
        _exigir(
            servico_antigo["status"] == "ativa" and servico_antigo["max_usuarios"] == 5,
            f"versão de retorno do serviço não leu o banco migrado: {servico_antigo}",
        )
        processo_servico.parar()
        processo_servico = iniciar_servico()

        processo_sicof.parar()
        rollback_sicof = temporario / "rollback-sicof"
        _extrair_ref(SICOF_DIR, REF_RETORNO_SICOF, rollback_sicof)
        processo_sicof = iniciar_sicof(rollback_sicof / "SICOF_NEXT", "sicof-retorno")
        sicof.close()
        sicof = _login_sicof(sicof_url)
        _exigir(sicof.get("/").status_code == 200,
                "versão de retorno do SICOF não abriu com cache e dados atuais")
        processo_sicof.parar()
        processo_sicof = iniciar_sicof()
        sicof.close()
        sicof = _login_sicof(sicof_url)
        _exigir(sicof.get("/").status_code == 200,
                "versão atual não voltou a abrir depois do ensaio de retorno")
        registrar(
            "10", "retorno de versão",
            f"serviço {REF_RETORNO_SERVICO} e SICOF {REF_RETORNO_SICOF} leem dados atuais",
            "ambas as versões anteriores abriram; versões atuais foram restauradas em seguida",
        )

        auditoria = _json_ok(httpx.get(
            f"{servico_url}/admin/auditoria", headers=headers, timeout=15,
        ))
        registros = auditoria.get("eventos", [])
        _exigir(isinstance(registros, list) and registros,
                "ações administrativas não apareceram na auditoria")
        _exigir(any(r.get("operador") == OPERADOR for r in registros),
                "operador da homologação não apareceu na auditoria")
        registrar(
            "evidencias", "auditoria administrativa",
            "ações são executadas pela API e atribuídas a um operador",
            f"{len(registros)} registros sanitizados foram encontrados",
        )

    except Exception as exc:
        falha = f"{type(exc).__name__}: {exc}"
        evidencias.append(Evidencia(
            "falha", "execução interrompida", "todos os cenários aprovados", falha, "reprovado",
        ))
    finally:
        if processo_sicof is not None:
            processo_sicof.parar()
        if processo_servico is not None:
            processo_servico.parar()

        banco_removido = False
        if admin_engine is not None:
            try:
                with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as con:
                    con.execute(text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:banco AND pid <> pg_backend_pid()"
                    ), {"banco": banco_nome})
                    con.execute(text(f'DROP DATABASE IF EXISTS "{banco_nome}"'))
                banco_removido = True
            finally:
                admin_engine.dispose()

        for log in logs_brutos.glob("*.log"):
            _sanitizar_log(
                log, evidencias_dir / log.name,
                (token_admin, auth_secret, privada_pem, banco_url),
            )

        resultado = {
            "etapa": 12,
            "tipo": "homologacao_local_isolada",
            "executado_em": _agora_iso(),
            "responsavel": args.responsavel,
            "versoes": {
                "servico": _git_commit(SERVICO_DIR),
                "sicof": _git_commit(SICOF_DIR),
                "retorno_servico": REF_RETORNO_SERVICO,
                "retorno_sicof": REF_RETORNO_SICOF,
            },
            "seguranca": {
                "chave_producao_usada": False,
                "token_producao_usado": False,
                "banco_producao_usado": False,
                "banco_descartavel_removido": banco_removido,
                "segredos_gravados_na_evidencia": False,
            },
            "resultado": "aprovado" if falha is None else "reprovado",
            "falha": falha,
            "cenarios": [asdict(item) for item in evidencias],
        }
        (evidencias_dir / "resultado.json").write_text(
            json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        shutil.rmtree(temporario, ignore_errors=True)

    if falha:
        print(f"HOMOLOGAÇÃO REPROVADA: {falha}", file=sys.stderr)
        print(f"Evidências: {evidencias_dir}", file=sys.stderr)
        return 1
    print(f"HOMOLOGAÇÃO APROVADA: {len(evidencias)} verificações")
    print(f"Evidências: {evidencias_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-admin-url", default="",
        help="URL PostgreSQL administrativa; por padrão usa TEST_DATABASE_URL",
    )
    parser.add_argument(
        "--evidencias", required=True,
        help="diretório em que resultado.json e logs sanitizados serão gravados",
    )
    parser.add_argument(
        "--responsavel", default="execucao-automatizada-codex",
        help="identificação não secreta do responsável pela execução",
    )
    return executar(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
