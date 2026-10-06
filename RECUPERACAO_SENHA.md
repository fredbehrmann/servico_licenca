# Operação do módulo de recuperação de senha

## Princípios

- A aba **Recuperação de senha** fica no mesmo `/admin` das licenças.
- O pedido `TFRQ1` não prova a identidade da pessoa; ele prova somente acesso à
  instalação e à tela de recuperação.
- O serviço nunca recebe a senha antiga ou a nova senha.
- O token `TFR1` não altera licença e só vale para a instalação, conta, pedido e
  desafio que o originaram.
- A privada de recuperação é exclusiva; não use a chave de licenças.

## Chave de produção v2

- `kid`: `recuperacao-prod-v2`
- pública base64: `mED0MK+2ol/3WKMSr/nZwkRfn6InuZ4qppM0qs7uN5w=`
- fingerprint SHA-256: `cec478bdac72767117d15f5effb7f33746a3f4ccc8e28ee4a4f5cb56cbae560b`

O arquivo privado gerado fica fora do repositório e deve ser transferido para o
cofre de segredos da Railway como `RECUPERACAO_PRIVADA_PEM`. Depois de conferir
o deploy e o backup offline, remova cópias de trabalho em texto claro.

Para criar uma chave de outro ambiente:

```powershell
python .\scripts\gerar_chave_recuperacao.py `
  --kid recuperacao-homolog-v2 `
  --saida-privada C:\caminho-fora-do-repositorio\recuperacao-homolog-v2.pem
```

O script recusa sobrescrita e destino dentro do repositório, e não imprime a
privada.

## Configuração OIDC

Cadastre um cliente web confidencial no provedor OIDC com callback HTTPS
`https://<dominio>/admin/callback`. Configure issuer, endpoints de autorização,
token e JWKS explicitamente. Crie grupos distintos para:

- manutenção de licenças;
- preparação de recuperação;
- aprovação/emissão de recuperação;
- leitura de auditoria.

Exija MFA no provedor. O serviço também confere `amr=mfa` ou `acrs=c1` no ID
token. A pessoa que prepara não pode aprovar a própria solicitação.

## Ordem de ativação

1. Distribua o SICOF que contém as públicas v1 e v2.
2. Aplique a migração 6 no PostgreSQL mantendo
   `RECUPERACAO_HABILITADA=false`.
3. Configure OIDC e a privada v2 na Railway.
4. Confirme que a pública derivada é exatamente a registrada acima.
5. Habilite em homologação e execute o ciclo completo com duas contas.
6. Habilite um grupo piloto em produção.
7. Mantenha o emissor DPAPI v1 somente para clientes antigos durante a janela
   de transição.

## Atendimento

1. Receba o `TFRQ1` pelo canal oficial.
2. Valide-o no painel e confira instalação, município, licença e prazo.
3. Confirme a identidade por método permitido e registre protocolo e
   justificativa.
4. Prepare o atendimento.
5. Um segundo operador entra com MFA, revisa e aprova.
6. O aprovador cola novamente o `TFRQ1`, digita `EMITIR` e gera o token.
7. Copie imediatamente o `TFR1`; a tela não o mostrará novamente.
8. Entregue-o somente pelo canal oficial. O usuário define a própria senha.

Instalação desconhecida exige `outro_escalonado` e confirmação expressa do
escalonamento. Estado de licença é contexto, não autorização automática para
recuperar senha.

## Incidentes e rotação

Se a privada for comprometida, desabilite `RECUPERACAO_HABILITADA`, preserve a
auditoria, gere outro par, publique a nova pública no cliente e somente depois
retome a emissão. Como a validação é offline, um token já emitido não pode ser
recolhido pelo serviço; a contenção usa validade curta, vínculo estrito e uso
único.

