# -*- coding: utf-8 -*-
"""Gera um par Ed25519 de recuperação sem imprimir a chave privada.

A saída privada deve ficar fora do repositório. O comando recusa sobrescrita e
imprime somente os metadados públicos necessários ao cliente e ao deploy.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _dentro(caminho: Path, raiz: Path) -> bool:
    try:
        caminho.resolve().relative_to(raiz.resolve())
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Gerar chave Ed25519 de recuperação")
    parser.add_argument("--kid", required=True)
    parser.add_argument("--saida-privada", type=Path, required=True)
    args = parser.parse_args()

    destino = args.saida_privada.expanduser().resolve()
    raiz_repo = Path(__file__).resolve().parents[1]
    if _dentro(destino, raiz_repo):
        parser.error("a chave privada deve ser salva fora do repositório")
    if destino.exists():
        parser.error("o arquivo de destino já existe; a chave não foi sobrescrita")
    if not args.kid.strip() or len(args.kid.strip()) > 80:
        parser.error("kid inválido")

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
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("xb") as arquivo:
        arquivo.write(pem)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    try:
        destino.chmod(0o600)
    except OSError:
        pass
    print(json.dumps({
        "kid": args.kid.strip(),
        "algoritmo": "Ed25519",
        "publica_b64": base64.b64encode(publica).decode("ascii"),
        "fingerprint_sha256": hashlib.sha256(publica).hexdigest(),
        "arquivo_privado": str(destino),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

