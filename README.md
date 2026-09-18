# Serviço de Licença — TechFisco

Serviço que emite a **licença assinada** consultada pelo app SICOF (Etapa 11). Projeto
**separado** do app: recebe os quatro campos da requisição, decide o status contra a allowlist,
assina com a chave privada de produção e devolve a resposta. É o **único componente exposto à
internet** — merece tratamento de serviço público (limite de requisições, registro de tentativas,
plano de indisponibilidade).

O contrato de dados e a assinatura são **byte-compatíveis** com o cliente do SICOF — verificado de
ponta a ponta (o serviço assina, o `licenca_cliente` do SICOF valida).

Autorizações antigas são convertidas pela ferramenta segura e idempotente descrita em
[`MIGRACAO_LEGADO.md`](MIGRACAO_LEGADO.md). A migração de dados nunca roda automaticamente durante
o startup; somente as alterações aditivas e versionadas do esquema são aplicadas na inicialização.

## Como funciona

- `POST /v1/consulta` — o app manda `{instalacao_id, codigo_ibge, versao_app, nonce}`; o serviço
  responde a licença assinada. Estados: `ativa`, `revogada`, `expirada` ou
  `instalacao_nao_autorizada`.
- `POST /admin/licencas` — cria a licença contratual, com vigência e limites reais.
- `PATCH /admin/licencas/{id}` — renova ou altera o estado e os limites da licença.
- `POST /admin/licencas/{id}/instalacoes` — associa ou altera uma instalação (`ativa`,
  `contingencia`, `revogada` ou `substituida`).
- `GET /admin/licencas` e `GET /admin/licencas/{id}/historico` — consulta contratos e auditoria.
- `POST /admin/autorizar` `{instalacao_id, codigo_ibge, max_usuarios?}` — compatibilidade com a
  allowlist antiga até sua migração na Etapa 4; não deve ser usado em novos contratos.
- `POST /admin/revogar` `{instalacao_id}` — revoga uma instalação.
- `POST /admin/revogar-municipio` `{codigo_ibge}` — revoga um município inteiro.
- `GET /admin/instalacoes` — lista as autorizadas.
- `GET /health` — informa apenas que o processo está vivo, sem revelar ambiente ou chave.
- `GET /ready` — prontidão real: configuração, chave/par esperado, banco e migrações.

- `GET /admin` — **painel web** de administração (página única). Cole o `ADMIN_TOKEN` e
  gerencie pela tela: crie e renove contratos, associe instalações, cadastre contingência, altere
  estados e consulte o histórico. A allowlist antiga permanece identificada numa seção separada
  somente durante a transição para a Etapa 4.

Endpoints `/admin/*` exigem cabeçalho `Authorization: Bearer <ADMIN_TOKEN>`. Em produção também
exigem `X-Admin-Operador`, preenchido pelo painel para identificar quem realizou a ação. O
`GET /admin`
(a página) é público — é só o formulário; nenhum dado carrega sem o token, que fica no navegador
e vai como cabeçalho nas chamadas. Acesse em `https://licenca.techfisco.com.br/admin`.

## Validação das entradas

A consulta pública aceita um objeto JSON de até 2 KiB e **exatamente** quatro campos. O
`instalacao_id` deve ser UUID canônico, `codigo_ibge` deve ser texto com sete dígitos,
`versao_app` deve usar formato como `1.2.3` e o `nonce` aceita no máximo 128 caracteres seguros.
Campos extras — inclusive dados pessoais ou fiscais enviados por engano — são recusados.

Corpos administrativos possuem limite de 16 KiB e também recusam campos desconhecidos. Datas
devem ser ISO 8601 com fuso horário. Limites devem ser números inteiros JSON: `max_auditores` e
`max_usuarios` ficam entre 1 e 10.000, `max_instalacoes_ativas` entre 1 e 100 e `dias_offline`
entre 1 e 30. Texto como `"5"`, zero, valores negativos e booleanos não são convertidos.

Reduzir `expira_em` exige confirmação explícita no mesmo PATCH:

```json
{
  "expira_em": "2027-01-31T23:59:59-03:00",
  "confirmar_encurtamento_vigencia": true
}
```

Erros de entrada respondem 400/422 sem rastreamento. Uma falha interna imprevista responde apenas
`{"detail":"erro interno"}`, sem devolver SQL, credencial ou detalhes do servidor.

## Variáveis de ambiente

| Variável | Obrigatória | Padrão | Papel |
|---|---|---|---|
| `LICENCA_PRIVADA_PEM` | **sim** | — | PEM da chave privada Ed25519 (segredo). Sem ela, o serviço não fica pronto. |
| `LICENCA_KEY_ID` | **sim** | — | `key_id` que vai na resposta; o app resolve a pública por ele. |
| `LICENCA_PUBLICA_B64_ESPERADA` | **sim** | — | Pública Ed25519 em base64 usada para conferir se a privada é o par correto. |
| `LICENCA_AMBIENTE` | **sim** | — | `producao` ou `homologacao`; não há produção implícita. |
| `ADMIN_TOKEN` | **sim em produção** | — | Segredo aleatório com pelo menos 32 caracteres para `/admin/*`. |
| `DATABASE_URL` | **sim** em produção | — | Postgres da Railway. Sem ela, usa repositório **em memória** (efêmero — só para teste local). |
| `LICENCA_VERSAO_MINIMA` | não | `1.0.0` | Padrão usado apenas pela allowlist antiga; contratos novos gravam a própria versão mínima. |
| `LICENCA_DIAS_VALIDADE` | não | `365` | Compatibilidade temporária da allowlist antiga. Contratos novos usam `expira_em` fixo. |
| `LICENCA_DIAS_OFFLINE` | não | `7` | Padrão legado. Cada contrato novo grava sua tolerância, que nunca ultrapassa `expira_em`. |
| `LICENCA_RATE_MAX` | não | `12` | Teto de consultas por instalação na janela. |
| `LICENCA_RATE_JANELA_S` | não | `3600` | Janela do teto, em segundos. |
| `LICENCA_REPLICAS` | **sim em produção** | — | Deve ser `1` enquanto o limitador for local ao processo. |
| `LICENCA_RETENCAO_TENTATIVAS_DIAS` | não | `90` | Retenção das consultas registradas. |
| `LICENCA_RETENCAO_AUDITORIA_DIAS` | não | `730` | Retenção de eventos de licença e ações administrativas. |

## Deploy na Railway

1. **Repositório.** Suba este diretório (`servico_licenca/`) como o projeto. Se ele viver dentro
   do repo do SICOF, aponte o *Root Directory* do serviço Railway para `servico_licenca`.
2. **Postgres.** Adicione o plugin PostgreSQL ao projeto — a Railway injeta `DATABASE_URL`
   automaticamente. O esquema é criado sozinho no primeiro start.
3. **Segredos.** Em *Variables*, defina `LICENCA_PRIVADA_PEM` (cole o PEM inteiro, multilinha — a
   Railway aceita), `LICENCA_KEY_ID`, `LICENCA_PUBLICA_B64_ESPERADA`,
   `LICENCA_AMBIENTE=producao`, `LICENCA_REPLICAS=1` e `ADMIN_TOKEN` (segredo aleatório forte).
4. **Start.** O `Procfile`/`railway.json` já sobem `uvicorn app.main:app`. O healthcheck de deploy
   usa `/ready`; `/health` sozinho não autoriza tráfego.
5. **Domínio.** Em *Settings → Networking*, adicione o domínio custom
   `licenca.techfisco.com.br` (produção) — a Railway te dá um alvo CNAME. No painel de DNS do
   `techfisco.com.br`, crie o CNAME do subdomínio apontando para esse alvo. A Railway emite o
   HTTPS. Repita com `licenca-homolog.techfisco.com.br` num serviço/ambiente de homologação.

> **Produção vs. homologação.** Use **chaves diferentes** por ambiente: `LICENCA_PRIVADA_PEM` de
> produção só no serviço de produção; para homologação, gere um par separado (nunca a chave de
> produção) e `LICENCA_AMBIENTE=homologacao`.

O procedimento completo de DNS/TLS, backup e restauração, rotação de credenciais, retenção e
operação com uma réplica está em [`OPERACAO_PRODUCAO.md`](OPERACAO_PRODUCAO.md).

## Chave de assinatura

A **pública** de produção já foi gerada e entregue ao app (`key_id: prod-ed25519-v1`,
registrada em `documentos/ENTRADAS_EXTERNAS_ETAPA_11.md`). A **privada** correspondente é o
`LICENCA_PRIVADA_PEM` — o PEM que ficou em custódia local na geração. Coloque-a **só** como
secret da Railway e mantenha um backup offline; nunca a comite.

Para gerar um par de **homologação** (separado do de produção):

```bash
python3 - <<'PY'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import base64
priv = Ed25519PrivateKey.generate()
print(priv.private_bytes(serialization.Encoding.PEM,
      serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print("public_b64 (para o app, em homologacao):", base64.b64encode(pub).decode())
PY
```

## Onboarding de uma prefeitura

1. A prefeitura instala o app, cria o primeiro administrador e confirma o código IBGE na tela de
   ativação restrita. Os módulos fiscais continuam bloqueados.
2. Ela informa à equipe TechFisco o `instalacao_id` exibido nessa tela.
3. No painel `/admin`, a equipe cria a licença contratual com a vigência e os limites acordados.
4. A equipe associa o `instalacao_id` à licença como instalação `ativa`.
5. O administrador municipal consulta novamente; uma resposta assinada válida libera os módulos.

Exemplo equivalente pela API:

```bash
curl -X POST https://licenca.techfisco.com.br/admin/licencas \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "X-Admin-Operador: fred" \
  -H "Content-Type: application/json" \
  -d '{"codigo_ibge":"2927408","status":"ativa","inicio_em":"2026-09-01T00:00:00-03:00","expira_em":"2027-08-31T23:59:59-03:00","max_auditores":5,"max_instalacoes_ativas":1,"dias_offline":7,"versao_minima":"1.0.0"}'
```

Use o `licenca_id` devolvido para chamar
`POST /admin/licencas/{licenca_id}/instalacoes` com
`{"instalacao_id":"<uuid-da-prefeitura>","status":"ativa"}`. Revogar, suspender ou renovar deve
ser feito no mesmo contrato, preservando seu histórico. A data final nunca é prolongada apenas
porque o aplicativo consultou novamente.

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Os testes cobrem contrato/canonização, validação estrita de entradas, decisão de status, consulta assinada, rate limit,
produção incompleta, prontidão, retenção, auditoria administrativa, vigência, instalações e
migração idempotente. A suíte comum roda com repositório em memória e, quando
`TEST_DATABASE_URL` está ausente, os cenários PostgreSQL são marcados como ignorados.

Para executar também a integração real, aponte `TEST_DATABASE_URL` para um banco em que o
usuário de teste possa criar e remover bancos descartáveis. Nenhum banco informado nessa URL é
apagado: a suíte cria bancos com prefixo `techfisco_teste_`, usa-os e remove somente esses bancos.

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres \
python -m pytest -q
```

O teste de contrato local procura o SICOF no diretório irmão `../sicof`. Outra localização pode
ser informada em `SICOF_REPO_DIR`. No GitHub Actions, o workflow baixa o cliente real, inicia
PostgreSQL 16 e executa a suíte inteira; por isso o banco em memória não é a única barreira de CI.

### Homologação ponta a ponta local

O roteiro completo da Etapa 12 pode ser repetido sem usar banco, chave, token ou endereço de
produção. O executor cria um banco temporário com prefixo `techfisco_homolog_`, gera credenciais
exclusivas da execução, inicia o serviço e o SICOF em `127.0.0.1`, percorre os dez cenários e
remove o banco ao final:

```bash
export TEST_DATABASE_URL='postgresql://usuario:senha@127.0.0.1:5432/postgres'
python scripts/homologar_fluxo_local.py \
  --evidencias /tmp/techfisco-etapa12 \
  --responsavel 'nome de quem executou'
```

O usuário PostgreSQL precisa poder criar e excluir bancos descartáveis. O programa nunca exclui
o banco informado em `TEST_DATABASE_URL`: ele cria outro, com nome aleatório e prefixo fechado, e
remove somente esse banco. A saída preservada contém `resultado.json` e logs sanitizados.

O teste cobre primeira ativação, vínculo municipal, assentos, papéis, redução contratual,
expiração, renovação, suspensão, revogação, modo off-line, reinício e retorno de versão. Os refs de
retorno são declarados no início do script e devem ser revisados antes de cada nova versão
candidata. Essa homologação local não substitui a conferência do DNS, HTTPS, backup e alertas na
infraestrutura externa.

## Compatibilidade — NÃO DIVERGIR

`app/contrato.py` (canonização + `payload_para_assinar`) é **cópia verbatim** de
`SICOF_NEXT/app/licenca_modelos.py`. Se o contrato mudar no SICOF, mude aqui junto e revalide: o
serviço assina uma resposta e o `licenca_cliente.verificar_assinatura` do SICOF tem de aceitá-la.
