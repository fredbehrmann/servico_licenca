# -*- coding: utf-8 -*-
"""Migração controlada da allowlist antiga para licenças contratuais.

O módulo não roda automaticamente no startup. O fluxo obrigatório é:

1. gerar o relatório/arquivo de decisões;
2. revisar e confirmar cada município;
3. criar e testar um backup externo do Postgres;
4. validar o arquivo contra o estado atual;
5. aplicar tudo em uma única transação.

Datas e limites ausentes nunca são inventados. Nesse caso, a decisão segura é
uma licença ``pendente`` com instalações de contingência, que não libera uso.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid5

from app import licencas
from app.repositorio import Repositorio, repositorio_do_ambiente


VERSAO_FORMATO = 1
_NAMESPACE = UUID("840a78d2-9063-4d9c-866f-3033df14da28")
_STATUS_COM_DATAS = frozenset({"ativa", "suspensa", "expirada"})


class ErroMigracao(ValueError):
    """O relatório ou as decisões não permitem uma migração segura."""


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(valor: datetime) -> str:
    return valor.astimezone(timezone.utc).isoformat()


def _inteiro_positivo(valor: Any, campo: str, *, obrigatorio: bool) -> Optional[int]:
    if valor in (None, ""):
        if obrigatorio:
            raise ErroMigracao(f"{campo} é obrigatório")
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError) as exc:
        raise ErroMigracao(f"{campo} deve ser um inteiro") from exc
    if numero < 1:
        raise ErroMigracao(f"{campo} deve ser maior que zero")
    return numero


def _snapshot(
    instalacoes: list[dict[str, Any]], revogados: list[str], contratos: list[dict[str, Any]],
) -> dict[str, Any]:
    legado = [{
        "instalacao_id": str(i.get("instalacao_id") or ""),
        "codigo_ibge": str(i.get("codigo_ibge") or ""),
        "max_usuarios": i.get("max_usuarios"),
        "revogada": bool(i.get("revogada")),
    } for i in instalacoes if not i.get("licenca_id")]
    existentes = [{
        "licenca_id": str(l.get("licenca_id") or ""),
        "codigo_ibge": str(l.get("codigo_ibge") or ""),
        "status": str(l.get("status") or ""),
        "inicio_em": l.get("inicio_em"),
        "expira_em": l.get("expira_em"),
        "max_auditores": l.get("max_auditores"),
        "max_instalacoes_ativas": l.get("max_instalacoes_ativas"),
        "dias_offline": l.get("dias_offline"),
        "versao_minima": l.get("versao_minima"),
    } for l in contratos]
    return {
        "instalacoes_legadas": sorted(legado, key=lambda i: (i["codigo_ibge"], i["instalacao_id"])),
        "municipios_revogados": sorted(str(c) for c in revogados),
        "licencas_existentes": sorted(
            existentes, key=lambda l: (l["codigo_ibge"], l["licenca_id"]),
        ),
    }


def _fingerprint(snapshot: dict[str, Any]) -> str:
    bruto = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def gerar_documento(
    repo: Repositorio, *, agora: Optional[datetime] = None,
    dias_offline_padrao: int = 7, versao_minima_padrao: str = "1.0.0",
) -> dict[str, Any]:
    """Gera relatório e decisões pré-preenchidas sem modificar o banco."""
    agora = (agora or _agora()).astimezone(timezone.utc)
    todas = repo.listar_instalacoes()
    revogados = repo.listar_municipios_revogados()
    contratos = repo.listar_licencas()
    snapshot = _snapshot(todas, revogados, contratos)
    fingerprint = _fingerprint(snapshot)
    legado = snapshot["instalacoes_legadas"]
    revogados_set = set(snapshot["municipios_revogados"])

    inconsistencias: list[dict[str, Any]] = []
    grupos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ids_municipios: dict[str, set[str]] = defaultdict(set)
    for instalacao in legado:
        iid = instalacao["instalacao_id"]
        ibge = instalacao["codigo_ibge"]
        ids_municipios[iid].add(ibge)
        if not ibge:
            inconsistencias.append({
                "tipo": "instalacao_sem_municipio", "gravidade": "impeditiva",
                "instalacao_id": iid,
                "orientacao": "corrigir o código do município no cadastro antes de aplicar",
            })
            continue
        grupos[ibge].append(instalacao)

    for iid, municipios in ids_municipios.items():
        if len(municipios) > 1:
            inconsistencias.append({
                "tipo": "instalacao_em_varios_municipios", "gravidade": "impeditiva",
                "instalacao_id": iid, "municipios": sorted(municipios),
                "orientacao": "definir e corrigir o município verdadeiro antes de aplicar",
            })

    contratos_por_ibge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for contrato in contratos:
        contratos_por_ibge[str(contrato.get("codigo_ibge") or "")].append(contrato)

    decisoes: list[dict[str, Any]] = []
    for ibge, itens in sorted(grupos.items()):
        nao_revogadas = [i for i in itens if not i["revogada"]]
        limites = sorted({
            int(i["max_usuarios"]) for i in itens
            if isinstance(i.get("max_usuarios"), int) and int(i["max_usuarios"]) > 0
        })
        for item in itens:
            limite = item.get("max_usuarios")
            if not isinstance(limite, int) or limite < 1:
                inconsistencias.append({
                    "tipo": "limite_ausente_ou_invalido", "gravidade": "decisao",
                    "codigo_ibge": ibge, "instalacao_id": item["instalacao_id"],
                    "valor_atual": limite,
                    "orientacao": "informar e confirmar max_auditores",
                })
        if len(limites) > 1:
            inconsistencias.append({
                "tipo": "limites_divergentes", "gravidade": "decisao",
                "codigo_ibge": ibge, "valores": limites,
                "orientacao": "confirmar um único limite contratual",
            })
        if len(nao_revogadas) > 1:
            inconsistencias.append({
                "tipo": "mais_de_uma_candidata_ativa", "gravidade": "decisao",
                "codigo_ibge": ibge,
                "instalacoes": sorted(i["instalacao_id"] for i in nao_revogadas),
                "orientacao": "escolher uma ativa e manter as demais como contingência",
            })
        if ibge in revogados_set and nao_revogadas:
            inconsistencias.append({
                "tipo": "municipio_revogado_com_instalacao_nao_revogada",
                "gravidade": "decisao", "codigo_ibge": ibge,
                "orientacao": "a migração sugere contrato e instalações revogados",
            })

        contratos_atuais = contratos_por_ibge.get(ibge, [])
        em_aberto = [
            l for l in contratos_atuais if l.get("status") in {"ativa", "suspensa", "pendente"}
        ]
        existente = em_aberto[0] if len(em_aberto) == 1 else None
        if contratos_atuais:
            inconsistencias.append({
                "tipo": "licenca_contratual_ja_existente", "gravidade": "decisao",
                "codigo_ibge": ibge,
                "licencas": sorted(str(l.get("licenca_id")) for l in contratos_atuais),
                "orientacao": "confirmar qual licença existente receberá as instalações",
            })
        if len(em_aberto) > 1:
            inconsistencias.append({
                "tipo": "varias_licencas_em_aberto", "gravidade": "impeditiva",
                "codigo_ibge": ibge,
                "orientacao": "regularizar os contratos antes da migração",
            })

        status_sugerido = "revogada" if ibge in revogados_set else (
            str(existente.get("status")) if existente else "pendente"
        )
        candidata = nao_revogadas[0]["instalacao_id"] if len(nao_revogadas) == 1 else None
        ativa = candidata if existente and status_sugerido in _STATUS_COM_DATAS else None
        contingencias = [] if status_sugerido == "revogada" else sorted(
            i["instalacao_id"] for i in nao_revogadas if i["instalacao_id"] != ativa
        )
        decisoes.append({
            "codigo_ibge": ibge,
            "confirmado": False,
            "status": status_sugerido,
            "inicio_em": existente.get("inicio_em") if existente else None,
            "expira_em": existente.get("expira_em") if existente else None,
            "max_auditores": (
                existente.get("max_auditores") if existente
                else limites[0] if len(limites) == 1 else None
            ),
            "max_instalacoes_ativas": (
                existente.get("max_instalacoes_ativas") if existente else 1
            ),
            "dias_offline": existente.get("dias_offline") if existente else dias_offline_padrao,
            "versao_minima": (
                existente.get("versao_minima") if existente else versao_minima_padrao
            ),
            "licenca_id_existente": existente.get("licenca_id") if existente else None,
            "instalacao_ativa_id": ativa,
            "instalacao_ativa_sugerida": candidata,
            "instalacoes_contingencia_ids": contingencias,
            "instalacoes_revogadas_ids": sorted(
                i["instalacao_id"] for i in itens if i["revogada"]
            ),
            "observacao": "",
        })

    for numero, item in enumerate(inconsistencias, 1):
        item["inconsistencia_id"] = f"INC-{numero:04d}"

    return {
        "versao_formato": VERSAO_FORMATO,
        "migracao_id": f"legado-v1-{fingerprint[:16]}",
        "fingerprint_origem": fingerprint,
        "gerado_em": _iso(agora),
        "resumo": {
            "total_instalacoes": len(todas),
            "instalacoes_legadas": len(legado),
            "instalacoes_ja_vinculadas": len(todas) - len(legado),
            "municipios_legados": len(grupos),
            "municipios_revogados": len(revogados_set),
            "inconsistencias": len(inconsistencias),
            "inconsistencias_impeditivas": sum(
                1 for i in inconsistencias if i["gravidade"] == "impeditiva"
            ),
        },
        "inconsistencias": inconsistencias,
        "decisoes": decisoes,
        "instrucoes": [
            "Revise somente a seção decisoes; não altere fingerprint_origem nem migracao_id.",
            "Defina confirmado=true para cada município depois de conferir os dados.",
            "Sem datas ou limite confirmados, mantenha status=pendente.",
            "Para status=ativa, informe datas, max_auditores e uma instalacao_ativa_id.",
            "Crie e teste um backup do Postgres imediatamente antes de aplicar.",
        ],
    }


def _datas_decisao(decisao: dict[str, Any], exige: bool) -> tuple[Optional[str], Optional[str]]:
    inicio_bruto, fim_bruto = decisao.get("inicio_em"), decisao.get("expira_em")
    if not inicio_bruto and not fim_bruto:
        if exige:
            raise ErroMigracao("inicio_em e expira_em são obrigatórios para o estado escolhido")
        return None, None
    if not inicio_bruto or not fim_bruto:
        raise ErroMigracao("inicio_em e expira_em devem ser informados juntos")
    inicio = licencas.parse_data(inicio_bruto, "inicio_em")
    fim = licencas.parse_data(fim_bruto, "expira_em")
    if fim <= inicio:
        raise ErroMigracao("expira_em deve ser posterior a inicio_em")
    return licencas.iso(inicio), licencas.iso(fim)


def preparar_planos(
    documento: dict[str, Any], repo: Repositorio, *, agora: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Valida decisões contra o banco atual e produz o lote para a transação."""
    agora = (agora or _agora()).astimezone(timezone.utc)
    if documento.get("versao_formato") != VERSAO_FORMATO:
        raise ErroMigracao("versão do arquivo de decisões não suportada")
    impeditivas = [
        i for i in documento.get("inconsistencias", []) if i.get("gravidade") == "impeditiva"
    ]
    if impeditivas:
        raise ErroMigracao(
            "existem inconsistências impeditivas; corrija a origem e gere um novo relatório"
        )

    instalacoes = [i for i in repo.listar_instalacoes() if not i.get("licenca_id")]
    grupos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in instalacoes:
        grupos[str(item.get("codigo_ibge") or "")].append(item)
    decisoes = documento.get("decisoes")
    if not isinstance(decisoes, list):
        raise ErroMigracao("seção decisoes ausente")
    por_ibge = {str(d.get("codigo_ibge") or ""): d for d in decisoes if isinstance(d, dict)}
    if len(por_ibge) != len(decisoes):
        raise ErroMigracao("há decisões duplicadas ou inválidas")
    if set(por_ibge) != set(grupos):
        raise ErroMigracao("os municípios das decisões não correspondem ao cadastro atual")

    contratos = {str(l.get("licenca_id")): l for l in repo.listar_licencas()}
    revogados = set(repo.listar_municipios_revogados())
    planos: list[dict[str, Any]] = []
    for ibge, itens in sorted(grupos.items()):
        decisao = por_ibge[ibge]
        if decisao.get("confirmado") is not True:
            raise ErroMigracao(f"município {ibge}: decisão ainda não confirmada")
        status = str(decisao.get("status") or "").strip().lower()
        if status not in licencas.STATUS_LICENCA:
            raise ErroMigracao(f"município {ibge}: status inválido")
        if ibge in revogados and status != "revogada":
            raise ErroMigracao(f"município {ibge}: município revogado deve permanecer revogado")

        existente_id = str(decisao.get("licenca_id_existente") or "")
        existente = contratos.get(existente_id) if existente_id else None
        if existente_id and existente is None:
            raise ErroMigracao(f"município {ibge}: licença existente não foi encontrada")
        if existente and str(existente.get("codigo_ibge")) != ibge:
            raise ErroMigracao(f"município {ibge}: licença existente pertence a outro município")
        if existente and str(existente.get("status")) != status:
            raise ErroMigracao(
                f"município {ibge}: altere o contrato existente antes da migração"
            )

        inicio, fim = _datas_decisao(decisao, status in _STATUS_COM_DATAS)
        max_auditores = _inteiro_positivo(
            decisao.get("max_auditores"), "max_auditores",
            obrigatorio=status in _STATUS_COM_DATAS,
        )
        max_instalacoes = _inteiro_positivo(
            decisao.get("max_instalacoes_ativas"), "max_instalacoes_ativas", obrigatorio=True,
        )
        dias_offline = _inteiro_positivo(
            decisao.get("dias_offline"), "dias_offline", obrigatorio=True,
        )
        versao_minima = str(decisao.get("versao_minima") or "").strip()
        if not versao_minima:
            raise ErroMigracao(f"município {ibge}: versao_minima é obrigatória")
        if existente:
            comparacoes = {
                "inicio_em": inicio,
                "expira_em": fim,
                "max_auditores": max_auditores,
                "max_instalacoes_ativas": max_instalacoes,
                "dias_offline": dias_offline,
                "versao_minima": versao_minima,
            }
            for campo, valor in comparacoes.items():
                atual = existente.get(campo)
                if campo in {"inicio_em", "expira_em"} and atual:
                    atual = licencas.iso(licencas.parse_data(atual, campo))
                if atual != valor:
                    raise ErroMigracao(
                        f"município {ibge}: {campo} diverge da licença existente; "
                        "atualize o contrato e gere outro relatório"
                    )

        ids = {str(i.get("instalacao_id")) for i in itens}
        ids_revogados = {str(i.get("instalacao_id")) for i in itens if i.get("revogada")}
        ids_disponiveis = ids - ids_revogados
        ativa = str(decisao.get("instalacao_ativa_id") or "")
        if ativa and ativa not in ids_disponiveis:
            raise ErroMigracao(f"município {ibge}: instalação ativa não é uma candidata válida")
        if status == "ativa" and not ativa:
            raise ErroMigracao(f"município {ibge}: escolha uma instalação ativa")
        if status in {"pendente", "revogada"} and ativa:
            raise ErroMigracao(f"município {ibge}: estado {status} não pode ter instalação ativa")

        contingencias = {str(i) for i in decisao.get("instalacoes_contingencia_ids", [])}
        esperadas = ids_disponiveis - ({ativa} if ativa else set())
        if status == "revogada":
            esperadas = set()
        if contingencias != esperadas:
            raise ErroMigracao(
                f"município {ibge}: a lista de contingência deve conter exatamente {sorted(esperadas)}"
            )
        if ativa and int(max_instalacoes or 1) < 1:
            raise ErroMigracao(f"município {ibge}: limite de instalações insuficiente")

        licenca_id = existente_id or str(uuid5(_NAMESPACE, f"legado:{ibge}"))
        chave = f"legado-v1:{ibge}"
        base_existente = existente or {}
        licenca = {
            "licenca_id": licenca_id,
            "codigo_ibge": ibge,
            "status": status,
            "inicio_em": inicio,
            "expira_em": fim,
            "max_auditores": max_auditores,
            "max_instalacoes_ativas": max_instalacoes,
            "dias_offline": dias_offline,
            "versao_minima": versao_minima,
            "criada_em": str(base_existente.get("criada_em") or _iso(agora)),
            "atualizada_em": str(base_existente.get("atualizada_em") or _iso(agora)),
            "chave_migracao": chave,
            "migrada_em": _iso(agora),
        }
        vinculos = []
        for item in sorted(itens, key=lambda i: str(i.get("instalacao_id"))):
            iid = str(item.get("instalacao_id"))
            if item.get("revogada") or status == "revogada":
                status_inst = "revogada"
            elif iid == ativa:
                status_inst = "ativa"
            else:
                status_inst = "contingencia"
            vinculos.append({
                "instalacao_id": iid, "status": status_inst,
                "codigo_ibge_esperado": str(item.get("codigo_ibge") or ""),
                "revogada_esperada": bool(item.get("revogada")),
                "max_usuarios_esperado": item.get("max_usuarios"),
            })
        planos.append({
            "licenca": licenca, "instalacoes": vinculos,
            "municipio_revogado_esperado": ibge in revogados,
        })
    return planos


def validar_documento(documento: dict[str, Any], repo: Repositorio) -> dict[str, Any]:
    atual = gerar_documento(repo)
    if str(documento.get("fingerprint_origem") or "") != atual["fingerprint_origem"]:
        raise ErroMigracao(
            "o cadastro mudou depois da geração do arquivo; gere e revise um novo relatório"
        )
    if str(documento.get("migracao_id") or "") != atual["migracao_id"]:
        raise ErroMigracao("migracao_id não corresponde ao relatório atual")
    if atual["resumo"]["inconsistencias_impeditivas"]:
        raise ErroMigracao(
            "existem inconsistências impeditivas; corrija a origem e gere um novo relatório"
        )
    planos = preparar_planos(documento, repo)
    return {
        "ok": True, "migracao_id": documento.get("migracao_id"),
        "municipios": len(planos),
        "instalacoes": sum(len(p["instalacoes"]) for p in planos),
    }


def aplicar_documento(
    documento: dict[str, Any], repo: Repositorio, backup_referencia: str,
    *, agora: Optional[datetime] = None,
) -> dict[str, Any]:
    backup = str(backup_referencia or "").strip()
    if not backup:
        raise ErroMigracao("a referência do backup restaurável é obrigatória")
    migracao_id = str(documento.get("migracao_id") or "")
    if not migracao_id:
        raise ErroMigracao("migracao_id ausente")
    anterior = repo.obter_migracao_dados(migracao_id)
    if anterior:
        return {**dict(anterior.get("resultado") or {}), "repetida": True}
    validar_documento(documento, repo)
    planos = preparar_planos(documento, repo, agora=agora)
    return repo.aplicar_migracao_legada(
        migracao_id, str(documento["fingerprint_origem"]), backup,
        planos, (agora or _agora()).astimezone(timezone.utc),
    )


def ler_documento(caminho: Path) -> dict[str, Any]:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ErroMigracao(f"não foi possível ler o arquivo: {exc}") from exc
    if not isinstance(dados, dict):
        raise ErroMigracao("o arquivo deve conter um objeto JSON")
    return dados


def salvar_documento(caminho: Path, dados: dict[str, Any], *, sobrescrever: bool = False) -> None:
    if caminho.exists() and not sobrescrever:
        raise ErroMigracao("o arquivo já existe; use --sobrescrever para substituí-lo")
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + f".{os.getpid()}.tmp")
    temporario.write_text(
        json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporario, 0o600)
    except OSError:
        pass
    os.replace(temporario, caminho)


def _repo_banco() -> Repositorio:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ErroMigracao("DATABASE_URL é obrigatória para a ferramenta de migração")
    return repositorio_do_ambiente(database_url)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Migração controlada da allowlist TechFisco")
    comandos = parser.add_subparsers(dest="comando", required=True)
    relatorio = comandos.add_parser("relatorio", help="gera relatório e arquivo de decisões")
    relatorio.add_argument("--saida", required=True, type=Path)
    relatorio.add_argument("--sobrescrever", action="store_true")
    validar = comandos.add_parser("validar", help="valida decisões sem modificar o banco")
    validar.add_argument("--entrada", required=True, type=Path)
    aplicar = comandos.add_parser("aplicar", help="aplica a migração em uma transação")
    aplicar.add_argument("--entrada", required=True, type=Path)
    aplicar.add_argument("--backup-referencia", required=True)
    aplicar.add_argument("--confirmar-backup", action="store_true")
    args = parser.parse_args(argv)

    try:
        repo = _repo_banco()
        if args.comando == "relatorio":
            documento = gerar_documento(repo)
            salvar_documento(args.saida, documento, sobrescrever=args.sobrescrever)
            resposta = {"ok": True, "saida": str(args.saida), **documento["resumo"]}
        elif args.comando == "validar":
            resposta = validar_documento(ler_documento(args.entrada), repo)
        else:
            if not args.confirmar_backup:
                raise ErroMigracao(
                    "confirme que o backup foi criado e restaurado em teste com --confirmar-backup"
                )
            resposta = aplicar_documento(
                ler_documento(args.entrada), repo, args.backup_referencia,
            )
        print(json.dumps(resposta, ensure_ascii=False, indent=2))
        return 0
    except ErroMigracao as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
