# -*- coding: utf-8 -*-
"""Painel web de licenças e recuperação de senha (servido em GET /admin).

Página única, autocontida (CSS/JS inline; a logo entra embutida como data-URI, sem
CDN). Não carrega nada sozinha: o operador cola o ADMIN_TOKEN, que fica só na sessão
do navegador e vai como cabeçalho Authorization nas chamadas aos endpoints /admin/* —
os mesmos que já existem e são protegidos por token. A página em si é pública (só o
formulário); nenhum dado aparece sem o token correto.

Visual alinhado à identidade do TechFisco (navy #0f2747 + verde #07805e, logo do
sistema). Mostra licenças contratuais, vigência, limites, instalações ativas e
de contingência, histórico, além da allowlist legada até sua migração — tudo sem
dado pessoal.
"""

from __future__ import annotations

import base64
from pathlib import Path


def _logo_data_uri() -> str:
    try:
        dados = (Path(__file__).with_name("logo.png")).read_bytes()
        return "data:image/png;base64," + base64.b64encode(dados).decode("ascii")
    except Exception:
        return ""


_LOGO = _logo_data_uri()
_MARCA = (
    f'<img src="{_LOGO}" alt="TechFisco" style="height:34px;display:block">'
    if _LOGO else '<span style="font-size:19px;font-weight:600;letter-spacing:.2px">TechFisco</span>'
)


_TEMPLATE = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TechFisco — Licenças</title>
<style>
  :root{--navy:#0f2747;--verde:#07805e;--verde-esc:#067556;--bg:#eef2f7;--surface:#fff;
        --surface2:#f6f9fc;--text:#12263f;--muted:#5b6b82;--border:#c3cfe0;
        --ok:#0a7a55;--ok-bg:#e7f8f2;--warn:#965d0d;--warn-bg:#fdf3e3;--alert:#b3312a;--alert-bg:#fdeae8;
        --radius:14px;--radius-sm:10px;--shadow:0 1px 3px rgba(15,39,71,.08),0 6px 18px rgba(15,39,71,.06);
        --font:"Segoe UI Variable Text","Segoe UI",Inter,system-ui,sans-serif;}
  *{box-sizing:border-box}
  body{font-family:var(--font);margin:0;background:var(--bg);color:var(--text);font-size:14px}
  header{background:var(--navy);color:#fff;padding:12px 22px;display:flex;align-items:center;gap:14px}
  header .div{width:1px;height:26px;background:rgba(255,255,255,.22)}
  header .titulo{font-size:16px;font-weight:600}
  header .amb{margin-left:auto;font-size:12px;color:#b9c7db}
  main{max-width:1180px;margin:0 auto;padding:22px}
  .cartao{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
          padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow)}
  h2{font-size:15px;margin:0 0 14px;color:var(--navy);display:flex;align-items:center;gap:8px;font-weight:600}
  h2 .dir{margin-left:auto;font-weight:400}
  label{display:block;font-size:12px;color:var(--muted);margin:8px 0 4px}
  input,select,textarea{width:100%;padding:9px 11px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:14px;
        background:var(--surface);color:var(--text)}
  textarea{min-height:96px;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:12px}
  input:focus,select:focus,textarea:focus{outline:2px solid var(--verde);outline-offset:1px;border-color:var(--verde)}
  .linha{display:flex;gap:12px;flex-wrap:wrap}
  .linha>div{flex:1;min-width:150px}
  button{background:var(--verde);color:#fff;border:0;border-radius:var(--radius-sm);padding:10px 16px;
         font-size:14px;font-weight:500;cursor:pointer}
  button:hover{background:var(--verde-esc)}
  button.sec{background:var(--surface);color:var(--verde);border:1px solid var(--verde)}
  button.sec:hover{background:var(--ok-bg)}
  button.perigo{background:var(--alert)}button.perigo:hover{filter:brightness(.94)}
  button.peq{padding:5px 11px;font-size:12px}
  button:disabled{opacity:.5;cursor:not-allowed}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:9px 6px;border-bottom:1px solid var(--border);vertical-align:middle}
  th{color:var(--muted);font-weight:500;font-size:12px}
  tr:last-child td{border-bottom:none}
  .cod{font-family:ui-monospace,Menlo,monospace;font-size:12px}
  .tag{font-size:11px;padding:3px 10px;border-radius:20px;white-space:nowrap;font-weight:500}
  .t-ativa{background:var(--ok-bg);color:var(--ok)}
  .t-revogada{background:var(--alert-bg);color:var(--alert)}
  .t-suspensa,.t-pendente,.t-contingencia{background:var(--warn-bg);color:var(--warn)}
  .t-expirada,.t-substituida{background:#eef2f7;color:var(--muted)}
  .t-naoautorizada{background:var(--warn-bg);color:var(--warn)}
  .t-outro{background:#eef2f7;color:var(--muted)}
  .resumo{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
        padding:14px 16px;box-shadow:var(--shadow)}
  .stat .n{font-size:28px;font-weight:600;line-height:1}
  .stat .l{font-size:12px;color:var(--muted);margin-top:5px}
  #aviso{padding:11px 15px;border-radius:var(--radius-sm);margin-bottom:14px;display:none;font-size:14px;font-weight:500}
  #aviso.ok{background:var(--ok-bg);color:var(--ok);display:block}
  #aviso.erro{background:var(--alert-bg);color:var(--alert);display:block}
  .vazio{color:var(--muted);padding:12px 4px;font-size:13px}
  .tabela-scroll{overflow-x:auto}
  .separador{border:0;border-top:1px solid var(--border);margin:20px 0}
  .consulta-resumo{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
                   gap:10px;margin:14px 0}
  .consulta-item{background:var(--surface2);border:1px solid var(--border);
                 border-radius:var(--radius-sm);padding:11px 12px}
  .consulta-item b{display:block;margin-bottom:5px;color:var(--navy)}
  .consulta-item span{display:block;color:var(--muted);font-size:12px;line-height:1.5}
  .paginacao{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:12px}
  .paginacao span{color:var(--muted);font-size:12px}
  .rodape{color:var(--muted);font-size:12px;text-align:center;margin:6px 0 24px}
  .abas{display:flex;gap:8px;margin:0 0 16px;flex-wrap:wrap}
  .abas button{background:var(--surface);color:var(--navy);border:1px solid var(--border)}
  .abas button.ativa{background:var(--navy);color:#fff;border-color:var(--navy)}
  .etapas{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}
  .etapa{padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface2)}
  .etapa b{display:block;color:var(--navy);margin-bottom:4px}.etapa span{font-size:12px;color:var(--muted)}
  .mensagem-fluxo{padding:10px 12px;border-radius:var(--radius-sm);margin:10px 0;background:var(--surface2);color:var(--muted)}
  .mensagem-fluxo.ok{background:var(--ok-bg);color:var(--ok)}.mensagem-fluxo.erro{background:var(--alert-bg);color:var(--alert)}
  .token-saida{background:#071a32;color:#fff;border-color:#071a32;min-height:130px}
  .checks label{display:flex;align-items:flex-start;gap:8px;color:var(--text);font-size:13px}
  .checks input{width:auto;margin-top:2px}.identidade{font-size:12px;color:var(--muted);margin:10px 0}
  fieldset{border:0;padding:0;margin:0}fieldset:disabled{opacity:.55}
  @media(max-width:680px){.resumo{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:680px){.etapas{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><span class="marca">__MARCA__</span><span class="div"></span>
  <span class="titulo">Administração de licenças e acessos</span><span class="amb" id="amb"></span></header>
<main>
  <div id="aviso"></div>

  <div class="cartao" id="box-acesso">
    <h2>Acesso</h2>
    <div id="sessao-info" class="identidade">Use sua conta corporativa para operações sensíveis.</div>
    <button id="btn-oidc" onclick="location.href='/admin/login'">Entrar com conta corporativa</button>
    <hr class="separador">
    <div class="vazio">A credencial compartilhada abaixo permanece apenas para as rotinas legadas de licença. Ela não autoriza recuperação de senha.</div>
    <label>Operador responsável</label>
    <input id="operador" placeholder="seu nome ou identificador corporativo" autocomplete="username">
    <label>Token de administração</label>
    <input id="token" type="password" placeholder="cole aqui o ADMIN_TOKEN" autocomplete="off"
           onkeydown="if(event.key==='Enter')entrar()">
    <div class="linha" style="margin-top:12px">
      <div style="flex:0"><button onclick="entrar()">Entrar</button></div>
      <div style="flex:0"><button class="sec" onclick="sair()">Sair</button></div>
      <div style="flex:0"><button class="sec" onclick="carregar()">Atualizar</button></div>
    </div>
  </div>

  <div id="painel" style="display:none">
    <div class="abas">
      <button id="aba-licencas" class="ativa" onclick="abrirArea('licencas')">Licenças</button>
      <button id="aba-recuperacao" onclick="abrirArea('recuperacao')">Recuperação de senha</button>
    </div>
    <section id="area-licencas">
    <div class="resumo">
      <div class="stat"><div class="n" style="color:var(--navy)" id="r-total">0</div><div class="l">instalações</div></div>
      <div class="stat"><div class="n" style="color:var(--ok)" id="r-ativas">0</div><div class="l">ativas</div></div>
      <div class="stat"><div class="n" style="color:var(--alert)" id="r-revogadas">0</div><div class="l">revogadas</div></div>
      <div class="stat"><div class="n" style="color:var(--warn)" id="r-munic">0</div><div class="l">municípios revogados</div></div>
    </div>

    <div class="cartao">
      <h2>Nova licença contratual</h2>
      <div class="linha">
        <div><label>Código IBGE</label><input id="l_ibge" placeholder="ex.: 2927408"></div>
        <div><label>Nome do município</label><input id="l_nome" maxlength="160" placeholder="ex.: Salvador"></div>
        <div><label>Início</label><input id="l_inicio" type="date"></div>
        <div><label>Expiração</label><input id="l_fim" type="date"></div>
        <div><label>Estado inicial</label><select id="l_status"><option value="pendente">pendente</option><option value="ativa">ativa</option></select></div>
      </div>
      <div class="linha">
        <div><label>Máx. auditores</label><input id="l_aud" type="number" min="1" value="5"></div>
        <div><label>Máx. instalações ativas</label><input id="l_inst" type="number" min="1" value="1"></div>
        <div><label>Dias sem internet</label><input id="l_off" type="number" min="1" value="7"></div>
        <div><label>Versão mínima</label><input id="l_ver" value="1.0.0"></div>
      </div>
      <button style="margin-top:12px" onclick="criarLicenca()">Criar licença</button>
    </div>

    <div class="cartao">
      <h2>Licenças contratuais</h2>
      <div class="tabela-scroll"><table>
        <thead><tr><th>Município</th><th>IBGE / licença</th><th>estado</th><th>vigência</th><th>auditores</th><th>instalações</th><th>ações</th></tr></thead>
        <tbody id="tab-lic"><tr><td colspan="7" class="vazio">Nenhuma licença contratual.</td></tr></tbody>
      </table></div>
    </div>

    <div class="cartao">
      <h2>Associar instalação à licença</h2>
      <div class="linha">
        <div><label>Licença</label><select id="v_lic"><option value="">selecione</option></select></div>
        <div><label>instalacao_id</label><input id="v_inst" placeholder="UUID exibido pelo TechFisco"></div>
        <div><label>Estado</label><select id="v_status"><option value="ativa">ativa</option><option value="contingencia">contingência</option><option value="revogada">revogada</option><option value="substituida">substituída</option></select></div>
      </div>
      <button style="margin-top:12px" onclick="associarInstalacao()">Associar instalação</button>
      <div id="historico" class="vazio"></div>
      <hr class="separador">
      <h2>Consultar instalações de uma licença</h2>
      <div class="linha">
        <div><label>Licença</label><select id="f_licenca" onchange="renderRelacionamentos(true)"><option value="">Todas</option></select></div>
        <div><label>Município</label><select id="f_municipio" onchange="renderRelacionamentos(true)"><option value="">Todos</option></select></div>
        <div><label>Estado da instalação</label><select id="f_estado" onchange="renderRelacionamentos(true)">
          <option value="">Todos</option><option value="ativa">ativa</option>
          <option value="contingencia">contingência</option><option value="revogada">revogada</option>
          <option value="substituida">substituída</option>
        </select></div>
        <div style="flex:0;align-self:flex-end"><button class="sec" onclick="limparFiltrosRelacionamentos()">Limpar</button></div>
      </div>
      <div id="resumo-relacionamentos" class="consulta-resumo"></div>
      <div class="tabela-scroll"><table>
        <thead><tr><th>Município</th><th>Licença</th><th>Instalação</th><th>Estado</th><th>Data e hora</th></tr></thead>
        <tbody id="tab-rel"><tr><td colspan="5" class="vazio">Nenhum relacionamento cadastrado.</td></tr></tbody>
      </table></div>
      <div class="paginacao">
        <button id="rel-anterior" class="sec peq" onclick="mudarPaginaRelacionamentos(-1)">Anterior</button>
        <span id="rel-pagina">Página 1 de 1</span>
        <button id="rel-proxima" class="sec peq" onclick="mudarPaginaRelacionamentos(1)">Próxima</button>
      </div>
    </div>

    <div class="cartao" id="box-legado">
      <h2>Autorizações antigas <span class="dir">compatibilidade até a Etapa 4</span></h2>
      <div class="linha">
        <div><label>instalacao_id (UUID)</label><input id="a_inst" placeholder="uuid da prefeitura"></div>
        <div><label>codigo_ibge</label><input id="a_ibge" placeholder="ex.: 2927408"></div>
        <div style="flex:0;min-width:130px"><label>máx. usuários</label><input id="a_max" type="number" min="1" placeholder="opcional"></div>
      </div>
      <button style="margin-top:12px" onclick="autorizar()">Autorizar</button>
    </div>

    <div class="cartao">
      <h2>Instalações <span class="dir"><input id="busca" placeholder="filtrar por UUID ou IBGE" style="width:220px" oninput="renderInstalacoes()"></span></h2>
      <table>
        <thead><tr><th>instalacao_id</th><th>IBGE</th><th>máx.</th><th>estado</th><th></th></tr></thead>
        <tbody id="tab-inst"><tr><td colspan="5" class="vazio">Entre com o token para carregar.</td></tr></tbody>
      </table>
    </div>

    <div class="cartao">
      <h2>Municípios revogados</h2>
      <div class="linha">
        <div><label>codigo_ibge</label><input id="m_ibge" placeholder="ex.: 2927408"></div>
        <div style="flex:0;align-self:flex-end"><button class="perigo" onclick="revogarMunicipio()">Revogar município</button></div>
      </div>
      <table style="margin-top:12px">
        <thead><tr><th>IBGE revogado</th><th></th></tr></thead>
        <tbody id="tab-munic"><tr><td colspan="2" class="vazio">—</td></tr></tbody>
      </table>
    </div>

    <div class="cartao">
      <h2>Consultas recentes <span class="dir"><button class="sec peq" onclick="carregar()">atualizar</button></span></h2>
      <table>
        <thead><tr><th>quando</th><th>instalacao_id</th><th>IBGE</th><th>resposta</th></tr></thead>
        <tbody id="tab-tent"><tr><td colspan="4" class="vazio">Sem consultas registradas ainda.</td></tr></tbody>
      </table>
      <div class="rodape" style="text-align:left;margin:10px 0 0">O serviço registra cada consulta (sem dado pessoal): identificador, município, horário e resposta.</div>
    </div>
    </section>

    <section id="area-recuperacao" style="display:none">
      <div class="cartao">
        <h2>Recuperação de senha administrativa</h2>
        <div class="etapas">
          <div class="etapa"><b>1. Validar</b><span>Confira a solicitação gerada pelo SICOF.</span></div>
          <div class="etapa"><b>2. Aprovar</b><span>Outro operador revisa o atendimento.</span></div>
          <div class="etapa"><b>3. Emitir</b><span>O token aparece uma única vez por 15 minutos.</span></div>
        </div>
        <div class="mensagem-fluxo">O suporte nunca deve pedir a senha atual ou a nova senha do usuário.</div>
        <label>Solicitação gerada no SICOF</label>
        <textarea id="rec_solicitacao" placeholder="Cole o texto iniciado por TFRQ1."></textarea>
        <button id="rec_btn_validar" style="margin-top:10px" onclick="validarRecuperacao()">Validar solicitação</button>
        <div id="rec_mensagem" class="mensagem-fluxo" style="display:none" role="status"></div>
        <div id="rec_resumo" class="consulta-resumo"></div>
      </div>

      <div class="cartao">
        <h2>Registrar atendimento</h2>
        <fieldset id="rec_preparo" disabled>
          <div class="linha">
            <div><label>Protocolo</label><input id="rec_protocolo" maxlength="120" placeholder="ex.: CHAMADO-2026-001"></div>
            <div><label>Método de verificação</label><select id="rec_metodo">
              <option value="">selecione</option>
              <option value="contato_oficial_cadastrado">Contato oficial cadastrado</option>
              <option value="videochamada_documentada">Videochamada documentada</option>
              <option value="presencial">Atendimento presencial</option>
              <option value="outro_escalonado">Outro — escalonado</option>
            </select></div>
          </div>
          <label>Justificativa da recuperação</label>
          <textarea id="rec_justificativa" maxlength="1000" placeholder="Descreva como a identidade foi conferida e por que a recuperação é necessária."></textarea>
          <div class="checks">
            <label><input id="rec_canal" type="checkbox"> O retorno será enviado somente pelo canal oficial confirmado.</label>
            <label><input id="rec_escalonamento" type="checkbox"> A exceção para instalação desconhecida foi escalonada e documentada.</label>
          </div>
          <button id="rec_btn_preparar" style="margin-top:10px" onclick="prepararRecuperacao()">Preparar para aprovação</button>
        </fieldset>
      </div>

      <div class="cartao">
        <h2>Aprovação e emissão</h2>
        <div id="rec_selecionada" class="mensagem-fluxo">Nenhuma solicitação preparada selecionada.</div>
        <div class="checks"><label><input id="rec_confirmar_aprovacao" type="checkbox"> Revisei o protocolo, a identidade e o contexto da instalação.</label></div>
        <div class="linha" style="margin-top:10px">
          <div style="flex:0"><button id="rec_btn_aprovar" disabled onclick="aprovarRecuperacao()">Aprovar</button></div>
          <div><input id="rec_confirmacao" placeholder="Para emitir, digite EMITIR" autocomplete="off"></div>
          <div style="flex:0"><button id="rec_btn_emitir" disabled onclick="emitirRecuperacao()">Emitir token</button></div>
        </div>
        <div id="rec_token_box" style="display:none;margin-top:14px">
          <label>Token — copie agora; ele não será exibido novamente</label>
          <textarea id="rec_token" class="token-saida" readonly></textarea>
          <div class="linha" style="margin-top:8px">
            <div style="flex:0"><button onclick="copiarTokenRecuperacao()">Copiar token</button></div>
            <div id="rec_contagem" class="identidade"></div>
          </div>
        </div>
      </div>

      <div class="cartao">
        <h2>Histórico saneado <span class="dir"><button class="sec peq" onclick="carregarRecuperacoes()">Atualizar</button></span></h2>
        <div class="tabela-scroll"><table>
          <thead><tr><th>Atualização</th><th>Protocolo</th><th>Instalação</th><th>Estado</th><th>Responsáveis</th><th></th></tr></thead>
          <tbody id="tab-rec"><tr><td colspan="6" class="vazio">Entre com a conta corporativa para consultar.</td></tr></tbody>
        </table></div>
      </div>
    </section>
  </div>

  <div class="rodape">As ações valem na próxima consulta do app. Revogação não volta a valer off-line.</div>
</main>

<script>
let _instalacoes = [];
let _licencas = [];
let _paginaRelacionamentos = 1;
let _sessao = {autenticado:false,papeis:[]};
let _csrf = '';
let _recuperacaoAtual = null;
let _intervaloToken = null;
const _porPaginaRelacionamentos = 10;
function tok(){ return sessionStorage.getItem('admtok') || ''; }
function operador(){ return sessionStorage.getItem('admoperador') || ''; }
function aviso(msg, ok){ const a=document.getElementById('aviso'); a.textContent=msg; a.className= ok?'ok':'erro';
  clearTimeout(window._av); window._av=setTimeout(()=>{a.className='';}, 4000); }
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function arg(s){ return encodeURIComponent(String(s==null?'':s)).replace(/'/g,'%27'); }
function tagStatus(s){
  const m={ativa:'t-ativa',revogada:'t-revogada',suspensa:'t-suspensa',pendente:'t-pendente',
    expirada:'t-expirada',contingencia:'t-contingencia',substituida:'t-substituida',
    instalacao_nao_autorizada:'t-naoautorizada'};
  return '<span class="tag '+(m[s]||'t-outro')+'">'+esc(s)+'</span>';
}
function quando(iso){
  if(!iso)return '—';
  const data=new Date(iso);
  return Number.isNaN(data.getTime()) ? '—' : data.toLocaleString('pt-BR');
}
function nomeMunicipio(licenca){
  return licenca&&licenca.nome_municipio ? licenca.nome_municipio : 'Nome não informado';
}
function rotuloLicenca(licenca){
  return nomeMunicipio(licenca)+' — '+licenca.codigo_ibge+' — '+licenca.status;
}

async function chamar(metodo, url, corpo){
  const headers={'Content-Type':'application/json'};
  if(tok())headers['Authorization']='Bearer '+tok();
  if(operador())headers['X-Admin-Operador']=operador();
  if(_csrf)headers['X-CSRF-Token']=_csrf;
  const r = await fetch(url, {
    method: metodo,
    credentials:'same-origin', headers,
    body: corpo ? JSON.stringify(corpo) : undefined
  });
  let dados={}; try{ dados=await r.json(); }catch(e){}
  if (r.status === 401){ aviso(_sessao.autenticado?'Sua sessão expirou. Entre novamente.':'Autenticação obrigatória.', false); throw new Error('401'); }
  if (!r.ok){ const detalhe=dados.detail||('Falha HTTP '+r.status); aviso(detalhe, false); throw new Error(detalhe); }
  return dados;
}

function entrar(){
  const t = document.getElementById('token').value.trim();
  const op = document.getElementById('operador').value.trim();
  if (!t){ aviso('Cole o token primeiro.', false); return; }
  if (!op){ aviso('Informe quem está realizando a operação.', false); return; }
  sessionStorage.setItem('admtok', t);
  sessionStorage.setItem('admoperador', op);
  carregar();
}
async function sair(){
  if(_sessao.autenticado){ try{ await chamar('POST','/admin/logout',{}); }catch(e){} }
  sessionStorage.removeItem('admtok'); sessionStorage.removeItem('admoperador');
  limparTokenRecuperacao(); _csrf=''; _sessao={autenticado:false,papeis:[]};
  document.getElementById('token').value=''; document.getElementById('operador').value='';
  document.getElementById('painel').style.display='none';
  document.getElementById('sessao-info').textContent='Sessão encerrada.';
}

function temPapel(papel){ return (_sessao.papeis||[]).includes(papel); }
function podeRecuperacao(){ return temPapel('recuperacao_operador')||temPapel('recuperacao_aprovador')||temPapel('auditoria_leitura'); }
function abrirArea(area){
  const rec=area==='recuperacao';
  if(rec&&!podeRecuperacao()){ aviso('Sua conta não possui perfil de recuperação.',false); return; }
  document.getElementById('area-licencas').style.display=rec?'none':'';
  document.getElementById('area-recuperacao').style.display=rec?'':'none';
  document.getElementById('aba-licencas').className=rec?'':'ativa';
  document.getElementById('aba-recuperacao').className=rec?'ativa':'';
  if(rec)carregarRecuperacoes();
}

async function inicializarSessao(){
  try{
    const r=await fetch('/admin/sessao',{credentials:'same-origin',headers:{'Accept':'application/json'}});
    _sessao=await r.json(); _csrf=_sessao.csrf||'';
  }catch(e){ _sessao={autenticado:false,papeis:[]}; }
  const info=document.getElementById('sessao-info');
  const btn=document.getElementById('btn-oidc');
  if(_sessao.autenticado){
    info.textContent='Conectado como '+(_sessao.nome||'operador')+(_sessao.mfa?' · MFA confirmado':'');
    btn.style.display='none'; document.getElementById('painel').style.display='';
    document.getElementById('aba-recuperacao').style.display=podeRecuperacao()&&_sessao.recuperacao_habilitada!==false?'':'none';
    document.getElementById('aba-licencas').style.display=temPapel('licencas_operador')?'':'none';
    document.getElementById('rec_btn_validar').disabled=!temPapel('recuperacao_operador');
    document.getElementById('rec_btn_preparar').disabled=!temPapel('recuperacao_operador');
    if(temPapel('licencas_operador'))carregar(); else if(podeRecuperacao())abrirArea('recuperacao');
  }else{
    btn.style.display=_sessao.oidc_habilitado===false?'none':'';
    document.getElementById('aba-recuperacao').style.display='none';
  }
}

async function carregar(){
  if (!tok()&&!(_sessao.autenticado&&temPapel('licencas_operador'))){ aviso('Entre com sua conta ou informe a credencial legada.', false); return; }
  try{
    const [inst, mr, tent, lic, mig, contexto] = await Promise.all([
      chamar('GET','/admin/instalacoes'),
      chamar('GET','/admin/municipios-revogados'),
      chamar('GET','/admin/tentativas?limite=100'),
      chamar('GET','/admin/licencas'),
      chamar('GET','/admin/migracao-legado/status'),
      chamar('GET','/admin/contexto')
    ]);
    document.getElementById('painel').style.display='';
    document.getElementById('amb').textContent = contexto && contexto.ambiente ? ('ambiente: '+contexto.ambiente) : '';
    _instalacoes = inst.instalacoes || [];
    _licencas = lic.licencas || [];
    document.getElementById('box-legado').style.display = mig.concluida ? 'none' : '';
    const ativas = _instalacoes.filter(i=>i.status_instalacao ? i.status_instalacao==='ativa' : !i.revogada).length;
    document.getElementById('r-total').textContent = _instalacoes.length;
    document.getElementById('r-ativas').textContent = ativas;
    document.getElementById('r-revogadas').textContent = _instalacoes.length - ativas;
    document.getElementById('r-munic').textContent = (mr.codigos||[]).length;
    renderInstalacoes();
    renderLicencas();
    popularFiltrosRelacionamentos();
    renderRelacionamentos(true);
    renderMunicipios(mr.codigos||[]);
    renderTentativas(tent.tentativas||[]);
    aviso('Carregado.', true);
  }catch(e){}
}

function dataCurta(iso){ return iso ? String(iso).slice(0,10) : '—'; }
function dataBrasileira(iso){
  const partes=String(iso||'').slice(0,10).split('-');
  return partes.length===3 ? partes[2]+'/'+partes[1]+'/'+partes[0] : 'data não informada';
}
function renderLicencas(){
  const tb=document.getElementById('tab-lic'); tb.innerHTML='';
  const sel=document.getElementById('v_lic'); sel.innerHTML='<option value="">selecione</option>';
  if(!_licencas.length){ tb.innerHTML='<tr><td colspan="7" class="vazio">Nenhuma licença contratual.</td></tr>'; return; }
  for(const l of _licencas){
    const id=arg(l.licenca_id);
    const op=document.createElement('option'); op.value=l.licenca_id;
    op.textContent=rotuloLicenca(l); sel.appendChild(op);
    const acoes='<button class="sec peq" onclick="renovarLicenca(\\''+id+'\\')">Renovar</button> '+
      '<button class="sec peq" onclick="editarNomeMunicipio(\\''+id+'\\')">Nome</button> '+
      '<button class="sec peq" onclick="alterarStatusLicenca(\\''+id+'\\',\\'ativa\\')">Ativar</button> '+
      '<button class="sec peq" onclick="alterarStatusLicenca(\\''+id+'\\',\\'suspensa\\')">Suspender</button> '+
      '<button class="perigo peq" onclick="alterarStatusLicenca(\\''+id+'\\',\\'revogada\\')">Revogar</button> '+
      '<button class="sec peq" onclick="verHistorico(\\''+id+'\\')">Histórico</button>';
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+esc(nomeMunicipio(l))+'</td>'+
      '<td>'+esc(l.codigo_ibge)+'<br><span class="cod">'+esc(l.licenca_id)+'</span></td>'+
      '<td>'+tagStatus(l.status)+'</td><td>'+dataCurta(l.inicio_em)+' a '+dataCurta(l.expira_em)+'</td>'+
      '<td>'+esc(l.max_auditores)+'</td><td>'+esc(l.instalacoes_ativas)+' / '+esc(l.max_instalacoes_ativas)+'</td>'+
      '<td>'+acoes+'</td>';
    tb.appendChild(tr);
  }
}

function popularFiltrosRelacionamentos(){
  const filtroLicenca=document.getElementById('f_licenca');
  const filtroMunicipio=document.getElementById('f_municipio');
  const licencaAtual=filtroLicenca.value;
  const municipioAtual=filtroMunicipio.value;
  filtroLicenca.innerHTML='<option value="">Todas</option>';
  for(const l of _licencas){
    const op=document.createElement('option');
    op.value=l.licenca_id; op.textContent=rotuloLicenca(l); filtroLicenca.appendChild(op);
  }
  const municipios=new Map();
  for(const l of _licencas){
    const atual=municipios.get(l.codigo_ibge);
    if(!atual||(!atual.nome_municipio&&l.nome_municipio))municipios.set(l.codigo_ibge,l);
  }
  filtroMunicipio.innerHTML='<option value="">Todos</option>';
  for(const l of [...municipios.values()].sort((a,b)=>nomeMunicipio(a).localeCompare(nomeMunicipio(b),'pt-BR'))){
    const op=document.createElement('option');
    op.value=l.codigo_ibge; op.textContent=nomeMunicipio(l)+' — '+l.codigo_ibge;
    filtroMunicipio.appendChild(op);
  }
  if([...filtroLicenca.options].some(o=>o.value===licencaAtual))filtroLicenca.value=licencaAtual;
  if([...filtroMunicipio.options].some(o=>o.value===municipioAtual))filtroMunicipio.value=municipioAtual;
}

function estadoDaInstalacao(instalacao){
  return instalacao.status_instalacao||(instalacao.revogada?'revogada':'ativa');
}
function relacionamentosFiltrados(){
  const licenca=document.getElementById('f_licenca').value;
  const municipio=document.getElementById('f_municipio').value;
  const estado=document.getElementById('f_estado').value;
  return _instalacoes.filter(i=>i.licenca_id)
    .filter(i=>!licenca||String(i.licenca_id)===licenca)
    .filter(i=>!municipio||String(i.codigo_ibge)===municipio)
    .filter(i=>!estado||estadoDaInstalacao(i)===estado)
    .sort((a,b)=>String(b.associada_em||'').localeCompare(String(a.associada_em||'')));
}
function renderResumoRelacionamentos(relacionamentos){
  const alvo=document.getElementById('resumo-relacionamentos');
  const licenca=document.getElementById('f_licenca').value;
  const municipio=document.getElementById('f_municipio').value;
  const idsVisiveis=new Set(relacionamentos.map(i=>String(i.licenca_id)));
  const filtradas=_licencas.filter(l=>(!licenca||String(l.licenca_id)===licenca)&&
    (!municipio||String(l.codigo_ibge)===municipio)&&
    (!document.getElementById('f_estado').value||idsVisiveis.has(String(l.licenca_id))));
  if(!filtradas.length){
    alvo.innerHTML='<div class="vazio">Nenhuma licença corresponde aos filtros informados.</div>';
    return;
  }
  alvo.innerHTML=filtradas.map(l=>'<div class="consulta-item"><b>'+esc(nomeMunicipio(l))+'</b>'+
    '<span>IBGE: '+esc(l.codigo_ibge)+'</span><span class="cod">Licença: '+esc(l.licenca_id)+'</span>'+
    '<span>Estado: '+esc(l.status)+'</span><span>Vigência: '+esc(dataCurta(l.inicio_em))+
    ' a '+esc(dataCurta(l.expira_em))+'</span><span>Auditores: '+esc(l.max_auditores)+
    ' · Instalações: '+esc(l.instalacoes_ativas)+' / '+esc(l.max_instalacoes_ativas)+'</span>'+
    '<span>Uso sem internet: '+esc(l.dias_offline)+' dia(s) · Versão mínima: '+
    esc(l.versao_minima)+'</span></div>').join('');
}
function renderRelacionamentos(reiniciar){
  if(reiniciar)_paginaRelacionamentos=1;
  const relacionamentos=relacionamentosFiltrados();
  const paginas=Math.max(1,Math.ceil(relacionamentos.length/_porPaginaRelacionamentos));
  _paginaRelacionamentos=Math.min(Math.max(1,_paginaRelacionamentos),paginas);
  const inicio=(_paginaRelacionamentos-1)*_porPaginaRelacionamentos;
  const pagina=relacionamentos.slice(inicio,inicio+_porPaginaRelacionamentos);
  const tb=document.getElementById('tab-rel'); tb.innerHTML='';
  if(!pagina.length){
    tb.innerHTML='<tr><td colspan="5" class="vazio">Nenhum relacionamento corresponde aos filtros.</td></tr>';
  }else{
    for(const i of pagina){
      const l=_licencas.find(item=>String(item.licenca_id)===String(i.licenca_id));
      const tr=document.createElement('tr');
      tr.innerHTML='<td>'+esc(nomeMunicipio(l))+'<br><span class="cod">'+esc(i.codigo_ibge)+'</span></td>'+
        '<td class="cod">'+esc(i.licenca_id)+'</td><td class="cod">'+esc(i.instalacao_id)+'</td>'+
        '<td>'+tagStatus(estadoDaInstalacao(i))+'</td><td>'+esc(quando(i.associada_em))+'</td>';
      tb.appendChild(tr);
    }
  }
  document.getElementById('rel-pagina').textContent='Página '+_paginaRelacionamentos+' de '+paginas+
    ' · '+relacionamentos.length+' relacionamento(s)';
  document.getElementById('rel-anterior').disabled=_paginaRelacionamentos<=1;
  document.getElementById('rel-proxima').disabled=_paginaRelacionamentos>=paginas;
  renderResumoRelacionamentos(relacionamentos);
}
function mudarPaginaRelacionamentos(delta){
  _paginaRelacionamentos+=delta;
  renderRelacionamentos(false);
}
function limparFiltrosRelacionamentos(){
  document.getElementById('f_licenca').value='';
  document.getElementById('f_municipio').value='';
  document.getElementById('f_estado').value='';
  renderRelacionamentos(true);
}

async function criarLicenca(){
  const ibge=document.getElementById('l_ibge').value.trim();
  const nome=document.getElementById('l_nome').value.trim();
  const ini=document.getElementById('l_inicio').value; const fim=document.getElementById('l_fim').value;
  if(!ibge||!nome||!ini||!fim){ aviso('Informe município, IBGE, início e expiração.', false); return; }
  const corpo={codigo_ibge:ibge,nome_municipio:nome,status:document.getElementById('l_status').value,
    inicio_em:ini+'T00:00:00+00:00',expira_em:fim+'T23:59:59+00:00',
    max_auditores:parseInt(document.getElementById('l_aud').value,10),
    max_instalacoes_ativas:parseInt(document.getElementById('l_inst').value,10),
    dias_offline:parseInt(document.getElementById('l_off').value,10),
    versao_minima:document.getElementById('l_ver').value.trim()};
  if(!confirm('Criar a licença de '+nome+' ('+ibge+') com término em '+fim+'?'))return;
  try{ await chamar('POST','/admin/licencas',corpo); aviso('Licença criada.',true); carregar(); }catch(e){}
}
async function editarNomeMunicipio(idCod){
  const id=decodeURIComponent(idCod);
  const atual=_licencas.find(l=>String(l.licenca_id)===id);
  const nome=prompt('Nome do município:',atual&&atual.nome_municipio?atual.nome_municipio:'');
  if(nome===null)return;
  if(!nome.trim()){ aviso('Informe o nome do município.',false); return; }
  try{ await chamar('PATCH','/admin/licencas/'+encodeURIComponent(id),{nome_municipio:nome.trim()});
    aviso('Nome do município atualizado.',true); carregar(); }catch(e){}
}
async function alterarStatusLicenca(idCod,status){
  const id=decodeURIComponent(idCod);
  if(!confirm('Alterar esta licença para '+status+'?'))return;
  try{ await chamar('PATCH','/admin/licencas/'+encodeURIComponent(id),{status}); aviso('Estado atualizado.',true); carregar(); }catch(e){}
}
async function renovarLicenca(idCod){
  const id=decodeURIComponent(idCod);
  const fim=prompt('Nova data final (AAAA-MM-DD):'); if(!fim)return;
  if(!confirm('Renovar esta licença até '+fim+'?'))return;
  try{ await chamar('PATCH','/admin/licencas/'+encodeURIComponent(id),{expira_em:fim+'T23:59:59+00:00',status:'ativa'}); aviso('Licença renovada.',true); carregar(); }catch(e){}
}
async function associarInstalacao(){
  const lid=document.getElementById('v_lic').value; const iid=document.getElementById('v_inst').value.trim();
  const status=document.getElementById('v_status').value;
  if(!lid||!iid){ aviso('Selecione a licença e informe o identificador da instalação.',false); return; }
  const existente=_instalacoes.find(i=>String(i.instalacao_id)===iid);
  const transferencia=!!(existente&&existente.licenca_id&&String(existente.licenca_id)!==String(lid));
  const corpo={instalacao_id:iid,status};
  if(transferencia){
    const anterior=_licencas.find(l=>String(l.licenca_id)===String(existente.licenca_id));
    const municipio=anterior&&anterior.codigo_ibge ? anterior.codigo_ibge : existente.codigo_ibge;
    const fim=dataBrasileira(anterior&&anterior.expira_em);
    const mensagem='Esta instalação já está associada à licença do município '+municipio+'.\\n\\n'+
      'A licença anterior continua válida até '+fim+'.\\n\\n'+
      'Confirma a alteração da instalação para a nova licença selecionada?';
    if(!confirm(mensagem))return;
    corpo.confirmar_transferencia=true;
  }else if(!confirm('Associar a instalação como '+status+'?'))return;
  try{ await chamar('POST','/admin/licencas/'+encodeURIComponent(lid)+'/instalacoes',corpo);
    aviso(transferencia?'Instalação transferida para a nova licença.':'Instalação associada.',true); carregar(); }catch(e){}
}
async function verHistorico(idCod){
  const id=decodeURIComponent(idCod);
  try{ const r=await chamar('GET','/admin/licencas/'+encodeURIComponent(id)+'/historico');
    const h=document.getElementById('historico');
    h.innerHTML='<b>Histórico da licença</b><br>'+(r.eventos.length?r.eventos.map(e=>esc(quando(e.quando))+' — '+esc(e.acao)+' — '+esc(e.detalhes)).join('<br>'):'Sem eventos.');
  }catch(e){}
}

function renderInstalacoes(){
  const f = (document.getElementById('busca').value||'').trim().toLowerCase();
  const tb = document.getElementById('tab-inst'); tb.innerHTML='';
  const lista = _instalacoes.filter(i => !f ||
    String(i.instalacao_id).toLowerCase().includes(f) || String(i.codigo_ibge).toLowerCase().includes(f));
  if (!lista.length){ tb.innerHTML='<tr><td colspan="5" class="vazio">'+(f?'Nada encontrado para o filtro.':'Nenhuma instalação autorizada ainda.')+'</td></tr>'; return; }
  for (const i of lista){
    const nomeEstado = i.status_instalacao || (i.revogada ? 'revogada' : 'ativa');
    const estado = tagStatus(nomeEstado);
    const acao = i.licenca_id
      ? '<button class="sec peq" onclick="mudarStatusInstalacao(\\''+arg(i.licenca_id)+'\\',\\''+arg(i.instalacao_id)+'\\',\\'ativa\\')">Ativar</button> '+
        '<button class="sec peq" onclick="mudarStatusInstalacao(\\''+arg(i.licenca_id)+'\\',\\''+arg(i.instalacao_id)+'\\',\\'contingencia\\')">Contingência</button> '+
        '<button class="perigo peq" onclick="mudarStatusInstalacao(\\''+arg(i.licenca_id)+'\\',\\''+arg(i.instalacao_id)+'\\',\\'revogada\\')">Revogar</button>'
      : (i.revogada
        ? '<button class="sec peq" onclick="reativar(\\''+arg(i.instalacao_id)+'\\',\\''+arg(i.codigo_ibge)+'\\','+(i.max_usuarios==null?'null':i.max_usuarios)+')">Reativar</button>'
        : '<button class="perigo peq" onclick="revogar(\\''+arg(i.instalacao_id)+'\\')">Revogar</button>');
    const tr=document.createElement('tr');
    tr.innerHTML='<td class="cod">'+esc(i.instalacao_id)+'</td><td>'+esc(i.codigo_ibge)+'</td>'+
      '<td>'+(i.max_usuarios==null?'—':esc(i.max_usuarios))+'</td><td>'+estado+'</td><td style="text-align:right">'+acao+'</td>';
    tb.appendChild(tr);
  }
}
function renderMunicipios(codigos){
  const tm=document.getElementById('tab-munic'); tm.innerHTML='';
  if (!codigos.length){ tm.innerHTML='<tr><td colspan="2" class="vazio">Nenhum.</td></tr>'; return; }
  for (const c of codigos){
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+esc(c)+'</td><td style="text-align:right"><button class="sec peq" onclick="reativarMunicipio(\\''+arg(c)+'\\')">Reativar</button></td>';
    tm.appendChild(tr);
  }
}
function renderTentativas(tent){
  const tt=document.getElementById('tab-tent'); tt.innerHTML='';
  if (!tent.length){ tt.innerHTML='<tr><td colspan="4" class="vazio">Sem consultas registradas ainda.</td></tr>'; return; }
  for (const t of tent){
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+quando(t.quando)+'</td><td class="cod">'+esc(t.instalacao_id)+'</td>'+
      '<td>'+esc(t.codigo_ibge)+'</td><td>'+tagStatus(t.status)+'</td>';
    tt.appendChild(tr);
  }
}

async function autorizar(){
  const inst=document.getElementById('a_inst').value.trim();
  const ibge=document.getElementById('a_ibge').value.trim();
  const maxv=document.getElementById('a_max').value.trim();
  if(!inst||!ibge){ aviso('instalacao_id e codigo_ibge são obrigatórios.', false); return; }
  const corpo={instalacao_id:inst, codigo_ibge:ibge};
  if(maxv) corpo.max_usuarios=parseInt(maxv,10);
  try{ await chamar('POST','/admin/autorizar',corpo); aviso('Instalação autorizada.', true);
    document.getElementById('a_inst').value=''; document.getElementById('a_ibge').value=''; document.getElementById('a_max').value='';
    carregar(); }catch(e){}
}
async function revogar(instCod){ const inst=decodeURIComponent(instCod); if(!confirm('Revogar a instalação '+inst+'?'))return;
  try{ await chamar('POST','/admin/revogar',{instalacao_id:inst}); aviso('Instalação revogada.', true); carregar(); }catch(e){} }
async function mudarStatusInstalacao(licencaCod,instCod,status){
  const licenca=decodeURIComponent(licencaCod); const inst=decodeURIComponent(instCod);
  if(!confirm('Alterar esta instalação para '+status+'?'))return;
  try{ await chamar('POST','/admin/licencas/'+encodeURIComponent(licenca)+'/instalacoes',
      {instalacao_id:inst,status}); aviso('Estado da instalação atualizado.',true); carregar(); }catch(e){} }
async function reativar(instCod,ibgeCod,maxu){ const inst=decodeURIComponent(instCod); const ibge=decodeURIComponent(ibgeCod); const corpo={instalacao_id:inst,codigo_ibge:ibge}; if(maxu!==null)corpo.max_usuarios=maxu;
  try{ await chamar('POST','/admin/autorizar',corpo); aviso('Instalação reativada.', true); carregar(); }catch(e){} }
async function revogarMunicipio(){ const ibge=document.getElementById('m_ibge').value.trim();
  if(!ibge){ aviso('Informe o codigo_ibge.', false); return; }
  if(!confirm('Revogar TODAS as instalações do município '+ibge+'?'))return;
  try{ await chamar('POST','/admin/revogar-municipio',{codigo_ibge:ibge}); aviso('Município revogado.', true);
    document.getElementById('m_ibge').value=''; carregar(); }catch(e){} }
async function reativarMunicipio(ibgeCod){ const ibge=decodeURIComponent(ibgeCod);
  try{ await chamar('POST','/admin/reativar-municipio',{codigo_ibge:ibge}); aviso('Município reativado.', true); carregar(); }catch(e){} }

function mensagemRecuperacao(texto,ok){
  const alvo=document.getElementById('rec_mensagem'); alvo.textContent=texto;
  alvo.className='mensagem-fluxo '+(ok?'ok':'erro'); alvo.style.display='block';
}
function dataEpoch(valor){
  const n=Number(valor); return n?new Date(n*1000).toLocaleString('pt-BR'):'—';
}
function resumoRecuperacao(d){
  const alerta=d.instalacao_conhecida?'Instalação localizada.':'Instalação não localizada — escalonamento obrigatório.';
  document.getElementById('rec_resumo').innerHTML=
    '<div class="consulta-item"><b>Validade</b><span>Solicitada em '+esc(dataEpoch(d.solicitado_em))+'</span><span>Expira em '+esc(dataEpoch(d.expira_em))+'</span></div>'+
    '<div class="consulta-item"><b>Instalação</b><span class="cod">'+esc(d.installation_id)+'</span><span>'+esc(alerta)+'</span></div>'+
    '<div class="consulta-item"><b>Conta</b><span>'+esc(d.usuario_mascarado)+'</span><span>Versão '+esc(d.versao_app)+'</span></div>'+
    '<div class="consulta-item"><b>Licença</b><span>'+esc(d.nome_municipio||'Não vinculada')+'</span><span>'+esc(d.status_licenca||d.status_instalacao||'sem contexto')+'</span></div>';
}
async function validarRecuperacao(){
  limparTokenRecuperacao();
  const solicitacao=document.getElementById('rec_solicitacao').value.trim();
  if(!solicitacao.startsWith('TFRQ1.')){ mensagemRecuperacao('Cole uma solicitação TFRQ1 válida.',false); return; }
  const botao=document.getElementById('rec_btn_validar'); botao.disabled=true;
  try{
    const r=await chamar('POST','/admin/recuperacoes/validar',{solicitacao});
    _recuperacaoAtual={request_id:r.solicitacao.request_id,estado:'validada'};
    resumoRecuperacao(r.solicitacao); document.getElementById('rec_preparo').disabled=false;
    mensagemRecuperacao('Solicitação válida. Confira o contexto antes de registrar o atendimento.',true);
  }catch(e){ document.getElementById('rec_preparo').disabled=true; }
  finally{ botao.disabled=!temPapel('recuperacao_operador'); }
}
async function prepararRecuperacao(){
  const solicitacao=document.getElementById('rec_solicitacao').value.trim();
  const corpo={solicitacao,
    protocolo:document.getElementById('rec_protocolo').value.trim(),
    justificativa:document.getElementById('rec_justificativa').value.trim(),
    metodo_verificacao:document.getElementById('rec_metodo').value,
    canal_oficial_confirmado:document.getElementById('rec_canal').checked,
    escalonamento_confirmado:document.getElementById('rec_escalonamento').checked};
  const botao=document.getElementById('rec_btn_preparar'); botao.disabled=true;
  try{
    const r=await chamar('POST','/admin/recuperacoes/preparar',corpo);
    _recuperacaoAtual=r.recuperacao; atualizarSelecionada();
    mensagemRecuperacao('Atendimento preparado. Outro operador deve aprová-lo.',true);
    await carregarRecuperacoes();
  }catch(e){}
  finally{ botao.disabled=!temPapel('recuperacao_operador'); }
}
function atualizarSelecionada(){
  const alvo=document.getElementById('rec_selecionada');
  if(!_recuperacaoAtual){ alvo.textContent='Nenhuma solicitação preparada selecionada.'; return; }
  alvo.textContent='Solicitação '+_recuperacaoAtual.request_id+' · estado: '+_recuperacaoAtual.estado+
    (_recuperacaoAtual.protocolo?' · protocolo: '+_recuperacaoAtual.protocolo:'');
  const aprovar=_recuperacaoAtual.estado==='preparada'&&temPapel('recuperacao_aprovador');
  const emitir=_recuperacaoAtual.estado==='aprovada'&&temPapel('recuperacao_aprovador');
  document.getElementById('rec_btn_aprovar').disabled=!aprovar;
  document.getElementById('rec_btn_emitir').disabled=!emitir;
}
function selecionarRecuperacao(idCod){
  const id=decodeURIComponent(idCod);
  const linha=(window._recuperacoes||[]).find(item=>String(item.request_id)===id);
  if(linha){ _recuperacaoAtual=linha; atualizarSelecionada(); limparTokenRecuperacao(); }
}
async function carregarRecuperacoes(){
  if(!_sessao.autenticado||!podeRecuperacao())return;
  try{
    const r=await chamar('GET','/admin/recuperacoes?limite=100');
    window._recuperacoes=r.recuperacoes||[];
    const tb=document.getElementById('tab-rec'); tb.innerHTML='';
    if(!window._recuperacoes.length){ tb.innerHTML='<tr><td colspan="6" class="vazio">Nenhuma recuperação registrada.</td></tr>'; return; }
    for(const item of window._recuperacoes){
      const responsaveis='Preparou: '+esc(item.operador_preparou||'—')+'<br>Aprovou: '+esc(item.aprovador||'—');
      const tr=document.createElement('tr');
      tr.innerHTML='<td>'+esc(dataEpoch(item.atualizado_em))+'</td><td>'+esc(item.protocolo)+'</td>'+
        '<td class="cod">'+esc(item.installation_id)+'</td><td>'+tagStatus(item.estado)+'</td>'+
        '<td>'+responsaveis+'</td><td><button class="sec peq">Selecionar</button></td>';
      tr.querySelector('button').addEventListener('click',()=>selecionarRecuperacao(encodeURIComponent(item.request_id)));
      tb.appendChild(tr);
    }
  }catch(e){}
}
async function aprovarRecuperacao(){
  if(!_recuperacaoAtual)return;
  if(!document.getElementById('rec_confirmar_aprovacao').checked){ mensagemRecuperacao('Confirme que realizou a revisão antes de aprovar.',false); return; }
  const botao=document.getElementById('rec_btn_aprovar'); botao.disabled=true;
  try{
    const r=await chamar('POST','/admin/recuperacoes/'+encodeURIComponent(_recuperacaoAtual.request_id)+'/aprovar',{confirmar:true});
    _recuperacaoAtual=r.recuperacao; atualizarSelecionada();
    mensagemRecuperacao('Solicitação aprovada. Apresente novamente o TFRQ1 e digite EMITIR.',true);
    await carregarRecuperacoes();
  }catch(e){ atualizarSelecionada(); }
}
async function emitirRecuperacao(){
  if(!_recuperacaoAtual)return;
  const solicitacao=document.getElementById('rec_solicitacao').value.trim();
  const confirmacao=document.getElementById('rec_confirmacao').value.trim();
  if(!solicitacao.startsWith('TFRQ1.')){ mensagemRecuperacao('Cole novamente a solicitação TFRQ1 aprovada.',false); return; }
  if(confirmacao!=='EMITIR'){ mensagemRecuperacao('Digite EMITIR para confirmar a emissão.',false); return; }
  const botao=document.getElementById('rec_btn_emitir'); botao.disabled=true;
  try{
    const r=await chamar('POST','/admin/recuperacoes/'+encodeURIComponent(_recuperacaoAtual.request_id)+'/emitir',{solicitacao,confirmacao});
    document.getElementById('rec_token').value=r.token;
    document.getElementById('rec_token_box').style.display='block';
    _recuperacaoAtual.estado='emitida'; atualizarSelecionada(); iniciarContagemToken(r.expira_em);
    mensagemRecuperacao('Token emitido. Copie-o agora e envie somente pelo canal oficial.',true);
    document.getElementById('rec_confirmacao').value=''; await carregarRecuperacoes();
  }catch(e){ atualizarSelecionada(); }
}
function iniciarContagemToken(expiraEm){
  if(_intervaloToken)clearInterval(_intervaloToken);
  const atualizar=()=>{ const restante=Math.max(0,Number(expiraEm)-Math.floor(Date.now()/1000));
    document.getElementById('rec_contagem').textContent=restante?'Expira em '+Math.floor(restante/60)+'m '+(restante%60)+'s':'Token expirado.';
    if(!restante&&_intervaloToken){clearInterval(_intervaloToken);_intervaloToken=null;}};
  atualizar(); _intervaloToken=setInterval(atualizar,1000);
}
async function copiarTokenRecuperacao(){
  const campo=document.getElementById('rec_token'); if(!campo.value)return;
  try{ await navigator.clipboard.writeText(campo.value); mensagemRecuperacao('Token copiado.',true); }
  catch(e){ campo.select(); document.execCommand('copy'); mensagemRecuperacao('Token copiado.',true); }
}
function limparTokenRecuperacao(){
  const campo=document.getElementById('rec_token'); if(campo)campo.value='';
  const caixa=document.getElementById('rec_token_box'); if(caixa)caixa.style.display='none';
  if(_intervaloToken){clearInterval(_intervaloToken);_intervaloToken=null;}
}

if (operador()) document.getElementById('operador').value=operador();
window.addEventListener('pagehide',limparTokenRecuperacao);
inicializarSessao().then(()=>{if(tok()&&operador()&&!_sessao.autenticado)carregar();});
</script>
</body>
</html>
"""

PAGINA_ADMIN = _TEMPLATE.replace("__MARCA__", _MARCA)
