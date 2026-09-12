# -*- coding: utf-8 -*-
"""Painel web de administração das licenças (servido em GET /admin).

Página única, autocontida (CSS/JS inline, sem CDN). Não carrega nada sozinha: o
operador cola o ADMIN_TOKEN, que fica só na memória/sessionStorage do navegador e
vai como cabeçalho Authorization nas chamadas aos endpoints /admin/* — os mesmos
que já existem e são protegidos por token. A página em si é pública (é só o
formulário); nenhum dado aparece sem o token correto.
"""

PAGINA_ADMIN = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TechFisco — Licenças</title>
<style>
  :root { --azul:#1b4f72; --cinza:#f4f6f8; --borda:#d8dee4; --vermelho:#b03a2e; --verde:#1e8449; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin:0;
         background:var(--cinza); color:#1c2833; }
  header { background:var(--azul); color:#fff; padding:14px 20px; font-size:18px; font-weight:600; }
  main { max-width: 980px; margin: 0 auto; padding: 20px; }
  .cartao { background:#fff; border:1px solid var(--borda); border-radius:10px;
            padding:16px 18px; margin-bottom:18px; }
  h2 { font-size:15px; margin:0 0 12px; color:var(--azul); }
  label { display:block; font-size:12px; color:#566573; margin:8px 0 3px; }
  input { width:100%; padding:8px 10px; border:1px solid var(--borda); border-radius:6px; font-size:14px; }
  .linha { display:flex; gap:10px; flex-wrap:wrap; }
  .linha > div { flex:1; min-width:140px; }
  button { background:var(--azul); color:#fff; border:0; border-radius:6px; padding:9px 14px;
           font-size:14px; cursor:pointer; margin-top:10px; }
  button.sec { background:#fff; color:var(--azul); border:1px solid var(--azul); }
  button.perigo { background:var(--vermelho); }
  button.pequeno { padding:5px 10px; font-size:12px; margin:0; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--borda); }
  th { color:#566573; font-weight:600; }
  .tag { font-size:11px; padding:2px 8px; border-radius:10px; }
  .tag.ativa { background:#eafaf1; color:var(--verde); }
  .tag.revogada { background:#fdedec; color:var(--vermelho); }
  #aviso { padding:10px 14px; border-radius:6px; margin-bottom:14px; display:none; font-size:14px; }
  #aviso.ok { background:#eafaf1; color:var(--verde); display:block; }
  #aviso.erro { background:#fdedec; color:var(--vermelho); display:block; }
  .cod { font-family: ui-monospace, Menlo, monospace; font-size:12px; }
  .rodape { color:#7f8c8d; font-size:12px; text-align:center; margin-top:8px; }
</style>
</head>
<body>
<header>TechFisco — Administração de Licenças</header>
<main>
  <div id="aviso"></div>

  <div class="cartao">
    <h2>Acesso</h2>
    <label>Token de administração</label>
    <input id="token" type="password" placeholder="cole aqui o ADMIN_TOKEN" autocomplete="off">
    <div class="linha">
      <div><button onclick="entrar()">Entrar</button></div>
      <div><button class="sec" onclick="sair()">Esquecer token</button></div>
    </div>
  </div>

  <div class="cartao">
    <h2>Autorizar / reativar instalação</h2>
    <div class="linha">
      <div><label>instalacao_id (UUID)</label><input id="a_inst" placeholder="uuid da prefeitura"></div>
      <div><label>codigo_ibge</label><input id="a_ibge" placeholder="ex.: 2927408"></div>
      <div><label>max_usuarios (opcional)</label><input id="a_max" type="number" min="1" placeholder="ex.: 5"></div>
    </div>
    <button onclick="autorizar()">Autorizar</button>
  </div>

  <div class="cartao">
    <h2>Instalações <button class="sec pequeno" onclick="carregar()" style="float:right">Atualizar</button></h2>
    <table>
      <thead><tr><th>instalacao_id</th><th>IBGE</th><th>máx.</th><th>estado</th><th></th></tr></thead>
      <tbody id="tabela"><tr><td colspan="5" style="color:#7f8c8d">Entre com o token para carregar.</td></tr></tbody>
    </table>
  </div>

  <div class="cartao">
    <h2>Municípios revogados</h2>
    <div class="linha">
      <div><label>codigo_ibge</label><input id="m_ibge" placeholder="ex.: 2927408"></div>
    </div>
    <button class="perigo" onclick="revogarMunicipio()">Revogar município inteiro</button>
    <table style="margin-top:14px">
      <thead><tr><th>IBGE revogado</th><th></th></tr></thead>
      <tbody id="tabela_munic"><tr><td colspan="2" style="color:#7f8c8d">—</td></tr></tbody>
    </table>
  </div>

  <div class="rodape">As ações valem na próxima consulta do app. Revogação não volta a valer off-line.</div>
</main>

<script>
function tok(){ return sessionStorage.getItem('admtok') || ''; }
function aviso(msg, ok){ const a=document.getElementById('aviso'); a.textContent=msg; a.className= ok?'ok':'erro'; }
function limpaAviso(){ document.getElementById('aviso').className=''; }

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
function sair(){ sessionStorage.removeItem('admtok'); document.getElementById('token').value=''; location.reload(); }

async function carregar(){
  try{
    limpaAviso();
    const dados = await chamar('GET', '/admin/instalacoes');
    const tb = document.getElementById('tabela'); tb.innerHTML='';
    if (!dados.instalacoes.length){ tb.innerHTML='<tr><td colspan="5" style="color:#7f8c8d">Nenhuma instalação autorizada ainda.</td></tr>'; }
    for (const i of dados.instalacoes){
      const tr = document.createElement('tr');
      const estado = i.revogada
        ? '<span class="tag revogada">revogada</span>'
        : '<span class="tag ativa">ativa</span>';
      const acao = i.revogada
        ? `<button class="sec pequeno" onclick="reativar('${i.instalacao_id}','${i.codigo_ibge}',${i.max_usuarios ?? 'null'})">Reativar</button>`
        : `<button class="perigo pequeno" onclick="revogar('${i.instalacao_id}')">Revogar</button>`;
      tr.innerHTML = `<td class="cod">${i.instalacao_id}</td><td>${i.codigo_ibge}</td>`+
                     `<td>${i.max_usuarios ?? '—'}</td><td>${estado}</td><td>${acao}</td>`;
      tb.appendChild(tr);
    }
    const mr = await chamar('GET', '/admin/municipios-revogados');
    const tm = document.getElementById('tabela_munic'); tm.innerHTML='';
    if (!mr.codigos.length){ tm.innerHTML='<tr><td colspan="2" style="color:#7f8c8d">Nenhum.</td></tr>'; }
    for (const c of mr.codigos){
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${c}</td><td><button class="sec pequeno" onclick="reativarMunicipio('${c}')">Reativar</button></td>`;
      tm.appendChild(tr);
    }
    aviso('Carregado.', true);
  }catch(e){}
}

async function autorizar(){
  const inst = document.getElementById('a_inst').value.trim();
  const ibge = document.getElementById('a_ibge').value.trim();
  const maxv = document.getElementById('a_max').value.trim();
  if (!inst || !ibge){ aviso('instalacao_id e codigo_ibge são obrigatórios.', false); return; }
  const corpo = { instalacao_id: inst, codigo_ibge: ibge };
  if (maxv) corpo.max_usuarios = parseInt(maxv, 10);
  try{ await chamar('POST', '/admin/autorizar', corpo); aviso('Instalação autorizada.', true); carregar(); }catch(e){}
}
async function revogar(inst){
  if (!confirm('Revogar a instalação ' + inst + '?')) return;
  try{ await chamar('POST', '/admin/revogar', { instalacao_id: inst }); aviso('Instalação revogada.', true); carregar(); }catch(e){}
}
async function reativar(inst, ibge, maxu){
  const corpo = { instalacao_id: inst, codigo_ibge: ibge };
  if (maxu !== null) corpo.max_usuarios = maxu;
  try{ await chamar('POST', '/admin/autorizar', corpo); aviso('Instalação reativada.', true); carregar(); }catch(e){}
}
async function revogarMunicipio(){
  const ibge = document.getElementById('m_ibge').value.trim();
  if (!ibge){ aviso('Informe o codigo_ibge.', false); return; }
  if (!confirm('Revogar TODAS as instalações do município ' + ibge + '?')) return;
  try{ await chamar('POST', '/admin/revogar-municipio', { codigo_ibge: ibge }); aviso('Município revogado.', true); carregar(); }catch(e){}
}
async function reativarMunicipio(ibge){
  try{ await chamar('POST', '/admin/reativar-municipio', { codigo_ibge: ibge }); aviso('Município reativado.', true); carregar(); }catch(e){}
}

if (tok()) carregar();
</script>
</body>
</html>
"""
