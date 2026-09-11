# Serviço de Licença — TechFisco

Serviço que emite a **licença assinada** consultada pelo app SICOF (Etapa 11). Projeto
**separado** do app: recebe os quatro campos da requisição, decide o status contra a allowlist,
assina com a chave privada de produção e devolve a resposta. É o **único componente exposto à
internet** — merece tratamento de serviço público (limite de requisições, registro de tentativas,
plano de indisponibilidade).

O contrato de dados e a assinatura são **byte-compatíveis** com o cliente do SICOF — verificado de
ponta a ponta (o serviço assina, o `licenca_cliente` do SICOF valida).

## Como funciona

- `POST /v1/consulta` — o app manda `{instalacao_id, codigo_ibge, versao_app, nonce}`; o serviço
  responde a licença assinada. Estados: `ativa`, `revogada`, `instalacao_nao_autorizada`.
- `POST /admin/autorizar` `{instalacao_id, codigo_ibge, max_usuarios?}` — autoriza uma instalação.
- `POST /admin/revogar` `{instalacao_id}` — revoga uma instalação.
- `POST /admin/revogar-municipio` `{codigo_ibge}` — revoga um município inteiro.
- `GET /admin/instalacoes` — lista as autorizadas.
- `GET /health`, `GET /ready` — vida e prontidão (chave + banco).

Endpoints `/admin/*` exigem cabeçalho `Authorization: Bearer <ADMIN_TOKEN>`.

## Variáveis de ambiente

| Variável | Obrigatória | Padrão | Papel |
|---|---|---|---|
| `LICENCA_PRIVADA_PEM` | **sim** | — | PEM da chave privada Ed25519 (segredo). Sem ela, `/v1/consulta` responde 503. |
| `LICENCA_KEY_ID` | recomendado | `prod-ed25519-v1` | `key_id` que vai na resposta; o app resolve a pública por ele. |
| `LICENCA_AMBIENTE` | recomendado | `producao` | `producao` ou `homologacao`. |
| `ADMIN_TOKEN` | **sim** (para admin) | — | Segredo do Bearer dos endpoints `/admin/*`. |
| `DATABASE_URL` | **sim** em produção | — | Postgres da Railway. Sem ela, usa repositório **em memória** (efêmero — só para teste local). |
| `LICENCA_VERSAO_MINIMA` | não | `1.0.0` | Versão mínima carimbada na resposta. |
| `LICENCA_DIAS_VALIDADE` | não | `365` | Validade da licença ativa. |
| `LICENCA_DIAS_OFFLINE` | não | `7` | Tolerância off-line (§ pergunta 5). Suba para redes instáveis. |
| `LICENCA_RATE_MAX` | não | `12` | Teto de consultas por instalação na janela. |
| `LICENCA_RATE_JANELA_S` | não | `3600` | Janela do teto, em segundos. |

## Deploy na Railway

1. **Repositório.** Suba este diretório (`servico_licenca/`) como o projeto. Se ele viver dentro
   do repo do SICOF, aponte o *Root Directory* do serviço Railway para `servico_licenca`.
2. **Postgres.** Adicione o plugin PostgreSQL ao projeto — a Railway injeta `DATABASE_URL`
   automaticamente. O esquema é criado sozinho no primeiro start.
3. **Segredos.** Em *Variables*, defina `LICENCA_PRIVADA_PEM` (cole o PEM inteiro, multilinha — a
   Railway aceita), `LICENCA_KEY_ID` (`prod-ed25519-v1`), `LICENCA_AMBIENTE` (`producao`) e
   `ADMIN_TOKEN` (um segredo forte).
4. **Start.** O `Procfile`/`railway.json` já sobem `uvicorn app.main:app`. Healthcheck em
   `/health`.
5. **Domínio.** Em *Settings → Networking*, adicione o domínio custom
   `licenca.techfisco.com.br` (produção) — a Railway te dá um alvo CNAME. No painel de DNS do
   `techfisco.com.br`, crie o CNAME do subdomínio apontando para esse alvo. A Railway emite o
   HTTPS. Repita com `licenca-homolog.techfisco.com.br` num serviço/ambiente de homologação.

> **Produção vs. homologação.** Use **chaves diferentes** por ambiente: `LICENCA_PRIVADA_PEM` de
> produção só no serviço de produção; para homologação, gere um par separado (nunca a chave de
> produção) e `LICENCA_AMBIENTE=homologacao`.

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

1. A prefeitura instala o app; ele exibe o `instalacao_id` (UUID) numa tela de administração.
2. Ela te informa o `instalacao_id` e o município (código IBGE).
3. Você autoriza:

```bash
curl -X POST https://licenca.techfisco.com.br/admin/autorizar \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"instalacao_id":"<uuid-da-prefeitura>","codigo_ibge":"2927408","max_usuarios":5}'
```

Revogar depois: `POST /admin/revogar` (uma instalação) ou `/admin/revogar-municipio` (o município
inteiro). A revogação vale na próxima consulta e, pelo desenho do cliente, não volta a valer
off-line.

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

23 testes: contrato/canonização, decisão de status, consulta assinada e verificável, rate limit,
503 sem chave, e administração (token, autorizar, revogar instalação/município). Rodam com
repositório em memória — não exigem Postgres.

## Compatibilidade — NÃO DIVERGIR

`app/contrato.py` (canonização + `payload_para_assinar`) é **cópia verbatim** de
`SICOF_NEXT/app/licenca_modelos.py`. Se o contrato mudar no SICOF, mude aqui junto e revalide: o
serviço assina uma resposta e o `licenca_cliente.verificar_assinatura` do SICOF tem de aceitá-la.
