# Migração da allowlist para licenças contratuais

Este roteiro converte as autorizações antigas sem inventar vigência, excluir colunas ou interromper
o contrato assinado usado pelo TechFisco.

> Não execute diretamente em produção sem antes ensaiar o backup e a restauração em um banco
> isolado. A ferramenta exige uma referência de backup, mas somente o operador consegue comprovar
> que esse backup é realmente restaurável.

## 1. O que a ferramenta garante

- o esquema é versionado em `migracoes_schema`;
- cada município recebe uma chave idempotente `legado-v1:<codigo_ibge>`;
- executar o mesmo arquivo novamente não duplica licenças nem histórico;
- o lote inteiro é aplicado em uma transação;
- `max_usuarios` e `revogada` antigos são preservados para auditoria e retorno;
- instalações e municípios são contados antes e depois;
- instalações revogadas continuam revogadas;
- licença ativa sem datas/limite ou instalação ativa sem licença faz a transação falhar;
- a allowlist continua legível até a aplicação do lote e suas colunas não são removidas.

A ferramenta não escolhe datas, limite de auditores ou instalação produtiva em nome do operador.
Quando essas informações faltarem, a sugestão é `pendente`, sem datas e sem liberação de uso.

## 2. Preparar homologação

Use uma cópia recente do banco produtivo em um ambiente isolado. Instale as dependências e configure
`DATABASE_URL` para essa cópia.

Ao iniciar o serviço, as migrações aditivas do esquema serão aplicadas e registradas. Confirme:

```sql
SELECT versao, nome, checksum, aplicada_em
FROM migracoes_schema
ORDER BY versao;
```

Devem existir as versões 1, 2 e 3.

## 3. Gerar o relatório e o arquivo de decisões

```bash
python -m app.migracao_legado relatorio \
  --saida decisoes-migracao-licencas.json
```

O arquivo nasce com permissão `0600`. Ele contém:

- resumo quantitativo;
- inconsistências impeditivas ou que exigem decisão;
- uma decisão pré-preenchida por município;
- impressão digital do estado do banco.

Edite somente a seção `decisoes`. Para cada município:

1. confira o código IBGE;
2. informe início, término e limite de auditores quando o contrato puder ficar ativo;
3. escolha `instalacao_ativa_id`;
4. confira as instalações de contingência;
5. confira o estado comercial;
6. altere `confirmado` para `true`.

Se os dados contratuais ainda não existirem, mantenha:

```json
{
  "status": "pendente",
  "inicio_em": null,
  "expira_em": null,
  "max_auditores": null,
  "instalacao_ativa_id": null
}
```

Instalações não revogadas devem permanecer na lista de contingência nesse caso.

## 4. Validar sem escrever

```bash
python -m app.migracao_legado validar \
  --entrada decisoes-migracao-licencas.json
```

A validação recusa o arquivo se o banco tiver mudado depois da geração. Nessa situação, gere outro
relatório e revise novamente; não copie a impressão digital manualmente.

## 5. Backup imediatamente anterior

Pare mudanças administrativas no serviço e gere um backup no formato custom do PostgreSQL:

```bash
pg_dump --format=custom --no-owner \
  --file techfisco-licencas-pre-migracao.dump "$DATABASE_URL"
```

Confira se o arquivo é legível:

```bash
pg_restore --list techfisco-licencas-pre-migracao.dump
```

Restaure esse arquivo em um banco temporário isolado e execute as conferências quantitativas. Apenas
listar o conteúdo não comprova que a restauração funciona. Registre o local seguro, o horário, o
responsável e o SHA-256 do arquivo. Essa identificação será a `backup-referencia`.

## 6. Aplicar

```bash
python -m app.migracao_legado aplicar \
  --entrada decisoes-migracao-licencas.json \
  --backup-referencia "<local-ou-ticket>#sha256=<hash>" \
  --confirmar-backup
```

Sem `--confirmar-backup` e uma referência não vazia, a ferramenta não inicia. O resultado informa os
quantitativos antes e depois. Execute o mesmo comando uma segunda vez: ele deve retornar
`"repetida": true` sem criar outro contrato ou evento.

## 7. Conferências após a aplicação

```sql
SELECT count(*) FROM instalacoes;
SELECT codigo_ibge, count(*) FROM instalacoes GROUP BY codigo_ibge ORDER BY codigo_ibge;
SELECT count(*) FROM instalacoes WHERE revogada = true;
SELECT count(*) FROM instalacoes WHERE licenca_id IS NULL;
SELECT licenca_id, codigo_ibge, status, inicio_em, expira_em, max_auditores
FROM licencas ORDER BY codigo_ibge;
```

O quarto comando deve retornar zero. Licenças `pendente` ou `revogada` podem não ter datas porque a
migração não as inventa; uma licença `ativa` nunca pode ficar incompleta.

Teste ainda:

- consulta de uma instalação ativa;
- consulta de uma instalação de contingência;
- consulta de uma instalação revogada;
- contrato vencido;
- repetição do comando de migração;
- painel administrativo e histórico.

## 8. Retorno

Se qualquer validação falhar durante a aplicação, a transação é desfeita automaticamente.

Se um problema for descoberto depois do commit da transação:

1. interrompa novas alterações administrativas;
2. preserve o banco pós-migração para investigação;
3. restaure o backup testado em um banco novo;
4. compare contagens e consultas no banco restaurado;
5. altere `DATABASE_URL` para o banco restaurado;
6. somente então reative o serviço;
7. registre o incidente e não reutilize o arquivo de decisões até entender a causa.

Não basta voltar apenas o código: instalações pendentes manteriam valores legados que não
representam um contrato aprovado. O retorno seguro é código compatível **e banco restaurado**.

## 9. O que não é removido nesta etapa

- `max_usuarios` e `revogada` em `instalacoes`;
- endpoints antigos de leitura e consulta;
- tratamento compatível no serviço;
- histórico de tentativas;
- municípios revogados.

A remoção dessas estruturas só poderá ocorrer depois do piloto produtivo e de uma janela formal de
retorno.
