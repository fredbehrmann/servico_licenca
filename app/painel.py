# -*- coding: utf-8 -*-
"""Painel web de administração das licenças (servido em GET /admin).

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
  main{max-width:1000px;margin:0 auto;padding:22px}
  .cartao{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
          padding:18px 20px;margin-bottom:16px;box-shadow:var(--shadow)}
  h2{font-size:15px;margin:0 0 14px;color:var(--navy);display:flex;align-items:center;gap:8px;font-weight:600}
  h2 .dir{margin-left:auto;font-weight:400}
  label{display:block;font-size:12px;color:var(--muted);margin:8px 0 4px}
  input,select{width:100%;padding:9px 11px;border:1px solid var(--border);border-radius:var(--radius-sm);font-size:14px;
        background:var(--surface);color:var(--text)}
  input:focus,select:focus{outline:2px solid var(--verde);outline-offset:1px;border-color:var(--verde)}
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
  .rodape{color:var(--muted);font-size:12px;text-align:center;margin:6px 0 24px}
  @media(max-width:680px){.resumo{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<header><span class="marca">__MARCA__</span><span class="div"></span>
  <span class="titulo">Administração de licenças</span><span class="amb" id="amb"></span></header>
<main>
  <div id="aviso"></div>

  <div class="cartao" id="box-acesso">
    <h2>Acesso</h2>
    <label>Token de administração</label>
    <input id="token" type="password" placeholder="cole aqui o ADMIN_TOKEN" autocomplete="off"
           onkeydown="if(event.key==='Enter')entrar()">
    <div class="linha" style="margin-top:12px">
      <div style="flex:0"><button onclick="entrar()">Entrar</button></div>
      <div style="flex:0"><button class="sec" onclick="sair()">Esquecer token</button></div>
      <div style="flex:0"><button class="sec" onclick="carregar()">Atualizar</button></div>
    </div>
  </div>

  <div id="painel" style="display:none">
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
      <table>
        <thead><tr><th>IBGE</th><th>estado</th><th>vigência</th><th>auditores</th><th>instalações</th><th>ações</th></tr></thead>
        <tbody id="tab-lic"><tr><td colspan="6" class="vazio">Nenhuma licença contratual.</td></tr></tbody>
      </table>
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
    </div>

    <div class="cartao">
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
  </div>

  <div class="rodape">As ações valem na próxima consulta do app. Revogação não volta a valer off-line.</div>
</main>

<script>
let _instalacoes = [];
let _licencas = [];
function tok(){ return sessionStorage.getItem('admtok') || ''; }
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
function quando(iso){ try{ return new Date(iso).toLocaleString('pt-BR'); }catch(e){ return esc(iso); } }

async function chamar(metodo, url, corpo){
  const r = await fetch(url, {
    method: metodo,
    headers: { 'Authorization': 'Bearer ' + tok(), 'Content-Type': 'application/json' },
    body: corpo ? JSON.stringify(corpo) : undefined
  });
  if (r.status === 401){ aviso('Token inválido. Confira o ADMIN_TOKEN.', false); throw new Error('401'); }
  if (!r.ok){ const t = await r.text(); aviso('Erro ' + r.status + ': ' + t, false); throw new Error(t); }
  return r.json();
}

function entrar(){
  const t = document.getElementById('token').value.trim();
  if (!t){ aviso('Cole o token primeiro.', false); return; }
  sessionStorage.setItem('admtok', t);
  carregar();
}
function sair(){ sessionStorage.removeItem('admtok'); document.getElementById('token').value='';
  document.getElementById('painel').style.display='none'; }

async function carregar(){
  if (!tok()){ aviso('Cole o token e clique em Entrar.', false); return; }
  try{
    const [inst, mr, tent, lic, saude] = await Promise.all([
      chamar('GET','/admin/instalacoes'),
      chamar('GET','/admin/municipios-revogados'),
      chamar('GET','/admin/tentativas?limite=100'),
      chamar('GET','/admin/licencas'),
      fetch('/health').then(r=>r.json()).catch(()=>({}))
    ]);
    document.getElementById('painel').style.display='';
    document.getElementById('amb').textContent = saude && saude.ambiente ? ('ambiente: '+saude.ambiente) : '';
    _instalacoes = inst.instalacoes || [];
    _licencas = lic.licencas || [];
    const ativas = _instalacoes.filter(i=>i.status_instalacao ? i.status_instalacao==='ativa' : !i.revogada).length;
    document.getElementById('r-total').textContent = _instalacoes.length;
    document.getElementById('r-ativas').textContent = ativas;
    document.getElementById('r-revogadas').textContent = _instalacoes.length - ativas;
    document.getElementById('r-munic').textContent = (mr.codigos||[]).length;
    renderInstalacoes();
    renderLicencas();
    renderMunicipios(mr.codigos||[]);
    renderTentativas(tent.tentativas||[]);
    aviso('Carregado.', true);
  }catch(e){}
}

function dataCurta(iso){ return iso ? String(iso).slice(0,10) : '—'; }
function renderLicencas(){
  const tb=document.getElementById('tab-lic'); tb.innerHTML='';
  const sel=document.getElementById('v_lic'); sel.innerHTML='<option value="">selecione</option>';
  if(!_licencas.length){ tb.innerHTML='<tr><td colspan="6" class="vazio">Nenhuma licença contratual.</td></tr>'; return; }
  for(const l of _licencas){
    const id=arg(l.licenca_id);
    const op=document.createElement('option'); op.value=l.licenca_id;
    op.textContent=l.codigo_ibge+' — '+l.status; sel.appendChild(op);
    const acoes='<button class="sec peq" onclick="renovarLicenca(\\''+id+'\\')">Renovar</button> '+
      '<button class="sec peq" onclick="alterarStatusLicenca(\\''+id+'\\',\\'ativa\\')">Ativar</button> '+
      '<button class="sec peq" onclick="alterarStatusLicenca(\\''+id+'\\',\\'suspensa\\')">Suspender</button> '+
      '<button class="perigo peq" onclick="alterarStatusLicenca(\\''+id+'\\',\\'revogada\\')">Revogar</button> '+
      '<button class="sec peq" onclick="verHistorico(\\''+id+'\\')">Histórico</button>';
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+esc(l.codigo_ibge)+'<br><span class="cod">'+esc(l.licenca_id)+'</span></td>'+
      '<td>'+tagStatus(l.status)+'</td><td>'+dataCurta(l.inicio_em)+' a '+dataCurta(l.expira_em)+'</td>'+
      '<td>'+esc(l.max_auditores)+'</td><td>'+esc(l.instalacoes_ativas)+' / '+esc(l.max_instalacoes_ativas)+'</td>'+
      '<td>'+acoes+'</td>';
    tb.appendChild(tr);
  }
}

async function criarLicenca(){
  const ibge=document.getElementById('l_ibge').value.trim();
  const ini=document.getElementById('l_inicio').value; const fim=document.getElementById('l_fim').value;
  if(!ibge||!ini||!fim){ aviso('Informe IBGE, início e expiração.', false); return; }
  const corpo={codigo_ibge:ibge,status:document.getElementById('l_status').value,
    inicio_em:ini+'T00:00:00+00:00',expira_em:fim+'T23:59:59+00:00',
    max_auditores:parseInt(document.getElementById('l_aud').value,10),
    max_instalacoes_ativas:parseInt(document.getElementById('l_inst').value,10),
    dias_offline:parseInt(document.getElementById('l_off').value,10),
    versao_minima:document.getElementById('l_ver').value.trim()};
  if(!confirm('Criar a licença do município '+ibge+' com término em '+fim+'?'))return;
  try{ await chamar('POST','/admin/licencas',corpo); aviso('Licença criada.',true); carregar(); }catch(e){}
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
  if(!confirm('Associar a instalação como '+status+'?'))return;
  try{ await chamar('POST','/admin/licencas/'+encodeURIComponent(lid)+'/instalacoes',{instalacao_id:iid,status}); aviso('Instalação associada.',true); carregar(); }catch(e){}
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

if (tok()) carregar();
</script>
</body>
</html>
"""

PAGINA_ADMIN = _TEMPLATE.replace("__MARCA__", _MARCA)
