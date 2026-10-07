# Operação do módulo de recuperação de senha

## Princípios

- A aba **Recuperação de senha** fica no mesmo `/admin` das licenças.
- O pedido `TFRQ1` não prova a identidade da pessoa; ele prova somente acesso à
  instalação e à tela de recuperação.
- O serviço nunca recebe a senha antiga ou a nova senha.
- O token `TFR1` não altera licença e só vale para a instalação, conta, pedido e
  desafio que o originaram.
- A privada de recuperação é exclusiva; não use a chave de licenças.
- Temporariamente, o painel usa `ADMIN_TOKEN` e o operador informado manualmente.
  Essa identificação serve para auditoria, mas não prova a identidade da pessoa.

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

## Autenticação administrativa temporária

Configure um `ADMIN_TOKEN` aleatório e exclusivo no cofre da Railway, com pelo
menos 32 caracteres e alta entropia. O painel envia esse valor como
`Authorization: Bearer <ADMIN_TOKEN>` e exige o nome ou identificador do operador
em `X-Admin-Operador` para registrar a auditoria.

Enquanto este modo estiver ativo:

- não compartilhe o token por e-mail, chat ou arquivo;
- restrinja o acesso à Railway e troque o token se houver suspeita de exposição;
- mantenha `RECUPERACAO_DUPLA_APROVACAO=false`, pois um token compartilhado não
  consegue provar que duas pessoas diferentes participaram;
- trate OIDC, MFA e papéis individuais como endurecimento pendente.

## Ordem de ativação

1. Distribua o SICOF que contém as públicas v1 e v2.
2. Aplique a migração 6 no PostgreSQL mantendo
   `RECUPERACAO_HABILITADA=false`.
3. Configure `ADMIN_TOKEN` forte e a privada v2 na Railway.
4. Confirme que a pública derivada é exatamente a registrada acima.
5. Habilite em homologação e execute o ciclo completo com o token administrativo.
6. Habilite um grupo piloto em produção.
7. Mantenha o emissor DPAPI v1 somente para clientes antigos durante a janela
   de transição.

## Atendimento

1. Receba o `TFRQ1` pelo canal oficial.
2. Valide-o no painel e confira instalação, município, licença e prazo.
3. Confirme a identidade por método permitido e registre protocolo e
   justificativa.
4. Prepare o atendimento.
5. O operador revisa os dados registrados e confirma a aprovação.
6. O operador cola novamente o `TFRQ1`, digita `EMITIR` e gera o token.
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
