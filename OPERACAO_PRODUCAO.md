# Operação segura do serviço de licenças

Este roteiro complementa o código. Ele separa as verificações automáticas do que depende da
infraestrutura da TechFisco e precisa ser executado por uma pessoa responsável.

## 1. Ambientes separados

Use dois serviços Railway e dois bancos PostgreSQL diferentes:

| Item | Homologação | Produção |
|---|---|---|
| Domínio | `licenca-homolog.techfisco.com.br` | `licenca.techfisco.com.br` |
| `LICENCA_AMBIENTE` | `homologacao` | `producao` |
| Banco | Postgres exclusivo de homologação | Postgres exclusivo de produção |
| Chave Ed25519 | par exclusivo de homologação | par exclusivo de produção |
| `ADMIN_TOKEN` | segredo exclusivo | segredo exclusivo |

Nunca copie o banco, a chave privada ou o token de produção para homologação. A chave pública
correspondente deve ser registrada em `LICENCA_PUBLICA_B64_ESPERADA`; o serviço compara esse valor
com a chave privada durante a prontidão.

## 2. Variáveis obrigatórias em produção

- `LICENCA_AMBIENTE=producao`;
- `DATABASE_URL` apontando para PostgreSQL;
- `LICENCA_PRIVADA_PEM` com a privada Ed25519;
- `LICENCA_KEY_ID` igual ao identificador confiado pelo aplicativo;
- `LICENCA_PUBLICA_B64_ESPERADA` com a pública correspondente;
- `ADMIN_TOKEN` aleatório, com ao menos 32 caracteres;
- `LICENCA_VERSAO_MINIMA` no formato `1.2.3`;
- `LICENCA_DIAS_OFFLINE` entre 1 e 30;
- `LICENCA_REPLICAS=1` enquanto o limitador continuar em memória.

O `/health` indica somente que o processo está vivo. O `/ready` só retorna HTTP 200 quando chave,
banco, migrações e configuração estão válidos. As consultas e a administração também falham
fechadas com HTTP 503 enquanto o serviço não estiver pronto.

## 3. Domínio, DNS e certificado

No cadastro DNS, registre e mantenha:

| Ambiente | CNAME | Alvo fornecido pela Railway | Responsável | Última conferência |
|---|---|---|---|---|
| Homologação | `licenca-homolog` | preencher no provisionamento | preencher | preencher |
| Produção | `licenca` | preencher no provisionamento | preencher | preencher |

Depois que o DNS propagar, execute:

```bash
./scripts/verificar_endpoint_https.sh https://licenca-homolog.techfisco.com.br
./scripts/verificar_endpoint_https.sh https://licenca.techfisco.com.br
```

Ative os alertas de expiração e falha de domínio no provedor usado pela equipe. Registre o nome do
responsável e faça uma conferência mensal. A renovação automática do certificado deve ser testada
antes de vencer, nunca apenas presumida.

## 4. Backup e restauração

Ative o backup automático do PostgreSQL no provedor. Além dele, gere uma cópia verificável antes
de migrações e mudanças relevantes:

```bash
export DATABASE_URL='postgresql://...'
export BACKUP_DIR='/diretorio/protegido/techfisco'
./scripts/backup_postgres.sh
```

Ao menos trimestralmente, restaure a cópia em um banco descartável que não seja produção:

```bash
export RESTORE_DATABASE_URL='postgresql://.../licencas_restore_test'
export CONFIRMAR_RESTAURACAO_TESTE=SIM
./scripts/restaurar_backup_teste.sh /caminho/techfisco-licencas-AAAA.dump
```

Registre data, responsável, arquivo/checksum, resultado e eventual correção. O código define
retenção padrão de 90 dias para tentativas e 730 dias para auditoria. Ajuste com
`LICENCA_RETENCAO_TENTATIVAS_DIAS` e `LICENCA_RETENCAO_AUDITORIA_DIAS` após decisão formal.

## 5. Migrações e conexões

As migrações são aditivas, versionadas, protegidas por trava no PostgreSQL e verificadas pelo
`/ready`. O pool testa a conexão antes de reutilizá-la e recicla conexões a cada cinco minutos.
Antes de publicar uma migração:

1. criar e verificar o backup;
2. testar a nova versão em homologação;
3. publicar uma única réplica em produção;
4. conferir `/ready` e os logs;
5. executar uma consulta real controlada.

## 6. Uma única réplica

O limitador de requisições ainda é local ao processo. Portanto a implantação inicial usa
`LICENCA_REPLICAS=1` e uma única réplica Railway. O `/ready` recusa produção se a variável declarar
outro valor. Antes de escalar horizontalmente, mova o contador para armazenamento compartilhado
(por exemplo, Redis) e crie testes concorrentes. Não aumente apenas o número de processos.

## 7. Administração e saída de integrante

O painel usa sessão OIDC individual em cookie `HttpOnly`, `Secure` e
`SameSite=Strict`. Escritas por sessão exigem CSRF, e a recuperação exige MFA e
grupos separados de operador e aprovador. O token `TFR1` existe somente na
resposta da emissão e na memória da página, nunca em `sessionStorage`.

O `ADMIN_TOKEN` e o identificador manual continuam somente para compatibilidade
das rotinas antigas de licença. Eles não autorizam endpoints de recuperação.
Toda chamada autenticada grava data, método, alvo, resultado e identidade sem
gravar credencial, solicitação ou token completos.

Para rotação ou saída de integrante:

1. gerar um novo `ADMIN_TOKEN` aleatório;
2. substituir o segredo no ambiente correto, sem enviá-lo por Git, log ou mensagem pública;
3. reiniciar e conferir `/ready`;
4. testar o novo token e confirmar que o antigo recebe HTTP 401;
5. remover o acesso da pessoa ao Railway, DNS, banco e cofre de segredos;
6. registrar o procedimento na auditoria interna.

Ao remover uma pessoa, retire-a dos grupos OIDC, encerre as sessões no provedor
e revise as emissões recentes. A remoção do grupo impede novas sessões; para um
incidente ativo, rotacione também `OIDC_SESSION_SECRET` para invalidar todas as
sessões locais.

## 8. Registro de validação externa

Os itens abaixo não podem ser concluídos apenas pelo código. Preencha durante o provisionamento:

- [ ] CNAME de homologação configurado, com responsável identificado.
- [ ] CNAME de produção configurado, com responsável identificado.
- [ ] Certificados válidos e endpoints conferidos pelo script.
- [ ] Backups automáticos do provedor habilitados.
- [ ] Um backup real criado e checksum guardado.
- [ ] Backup restaurado com sucesso em banco descartável.
- [ ] Bancos, chaves e tokens comparados e confirmados como distintos.
- [ ] Railway configurada com uma única réplica em produção.
- [ ] Cliente com a pública `recuperacao-prod-v2` distribuído antes da ativação.
- [ ] OIDC, MFA e grupos de operador/aprovador validados com duas contas reais.
- [ ] Chave de recuperação distinta da chave de licença e pública conferida.
- [ ] Fluxo `TFRQ1` → aprovação → `TFR1` testado em instalação limpa.
