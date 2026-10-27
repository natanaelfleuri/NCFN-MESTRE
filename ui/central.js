// Central: meus arquivos (retrato e sugestões), programas instalados (com
// explicação pela IA) e integração de pastas/projetos em fluxos de produtividade.

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then((r) => r.json());

export function human(n) {
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i < 2 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}
function ago(ts) {
  if (!ts) return '';
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return `há ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `há ${Math.round(s / 3600)} h`;
  if (s < 86400 * 60) return `há ${Math.round(s / 86400)} dias`;
  return `há ${Math.round(s / 86400 / 30)} meses`;
}
const short = (p) => String(p).replace(/^\/home\/[^/]+/, '~');

// markdown mínimo (títulos, listas, negrito, código) — sempre escapado
function md(text) {
  const out = [];
  let list = false;
  for (const raw of String(text).split('\n')) {
    const line = esc(raw).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
    const h = raw.match(/^(#{1,4})\s/);
    const li = raw.match(/^\s*([-*]|\d+[.)])\s+/);
    if (li) {
      if (!list) { out.push('<ul>'); list = true; }
      out.push(`<li>${line.replace(/^\s*([-*]|\d+[.)])\s+/, '')}</li>`);
      continue;
    }
    if (list) { out.push('</ul>'); list = false; }
    if (h) out.push(`<h4>${line.replace(/^#+\s/, '')}</h4>`);
    else if (raw.startsWith('```')) continue;
    else if (raw.trim()) out.push(`<p>${line}</p>`);
  }
  if (list) out.push('</ul>');
  return out.join('');
}

export function initCentral({ send, stopSpeech, addLog }) {
  const st = { files: null, apps: null, src: 'apps', cat: 'Todos', q: '', ia: {}, job: null, projects: [], flows: [], picked: new Set() };

  const ask = (text) => {
    $('#central').classList.add('hidden');
    stopSpeech();
    send({ type: 'text', text });
  };

  function tab(t) {
    $$('#central-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.ctab === t));
    $$('#central [data-cpane]').forEach((p) => p.classList.toggle('hidden', p.dataset.cpane !== t));
    if (t === 'files' && !st.files) loadFiles();
    if (t === 'apps' && !st.apps) loadApps();
    if (t === 'flows' && !st.projects.length) { loadProjects(); loadFlows(); }
  }
  $$('#central-tabs button').forEach((b) => (b.onclick = () => tab(b.dataset.ctab)));
  $('#open-central').onclick = () => {
    $('#central').classList.remove('hidden');
    tab($('#central-tabs .on')?.dataset.ctab || 'files');
  };
  $('#central-close').onclick = () => $('#central').classList.add('hidden');

  // ---------------------------------------------------------- arquivos
  async function loadFiles(refresh = false) {
    $('#cf-note').textContent = refresh ? 'escaneando as pastas…' : 'carregando…';
    try {
      st.files = await (await fetch(`/api/central/files${refresh ? '?refresh=true' : ''}`)).json();
      renderFiles();
    } catch { $('#cf-note').textContent = 'erro ao escanear'; }
  }
  function renderFiles() {
    const r = st.files;
    $('#cf-note').textContent = `retrato de ${new Date(r.quando * 1000).toLocaleString('pt-BR')}${r.incompleto ? ' (parcial: pastas muito grandes)' : ''}`;
    const tiles = $('#cf-folders');
    tiles.innerHTML = '';
    for (const [name, f] of Object.entries(r.pastas)) {
      const t = document.createElement('button');
      t.className = 'tile';
      t.title = `Abrir ${f.caminho}`;
      t.innerHTML = `<b>${esc(name)}</b><span>${f.arquivos.toLocaleString('pt-BR')} arquivos</span><i>${human(f.tamanho)}</i><em>${f.recentes_30d} mexidos em 30 dias</em>`;
      t.onclick = () => post('/api/central/open', { path: f.caminho });
      tiles.append(t);
    }
    const total = Object.values(r.tipos).reduce((n, t) => n + t.tamanho, 0) || 1;
    $('#cf-types').innerHTML = Object.entries(r.tipos).map(([c, t], i) =>
      `<span style="flex:${Math.max(t.tamanho / total, 0.015)}" class="ty${i % 8}" title="${esc(c)}: ${t.arquivos} arquivos, ${human(t.tamanho)}"><b>${esc(c)}</b> ${human(t.tamanho)}</span>`).join('');
    const sugs = $('#cf-sugs');
    sugs.innerHTML = '';
    for (const s of r.sugestoes) {
      const li = document.createElement('li');
      li.innerHTML = `<div class="ttl"><span class="lvl ${s.nivel}">${s.nivel === 'alta' ? 'prioridade' : s.nivel === 'media' ? 'média' : 'dica'}</span> ${esc(s.titulo)}</div>
        <div class="sum">${esc(s.detalhe)}</div><div class="acts"></div>`;
      if (s.pedido) {
        const b = document.createElement('button');
        b.textContent = '▶ Pedir ao MESTRE';
        b.title = s.pedido;
        b.onclick = () => ask(s.pedido);
        li.querySelector('.acts').append(b);
      } else {
        const b = document.createElement('button');
        b.textContent = '⏱ Agendar triagem';
        b.onclick = () => { tab('flows'); setTimeout(() => $('#fl-list [data-id="triagem-downloads"]')?.scrollIntoView({ block: 'center' }), 400); };
        li.querySelector('.acts').append(b);
      }
      sugs.append(li);
    }
    if (r.analise?.texto) showAnalysis(r.analise);
    const rows = (ul, items, head, title) => {
      $(head).textContent = title;
      const el = $(ul);
      el.innerHTML = '';
      for (const it of items) {
        const li = document.createElement('li');
        li.innerHTML = `<div class="ttl mono"></div><div class="meta"></div><div class="acts"><button>📂 Abrir pasta</button></div>`;
        li.querySelector('.ttl').textContent = it.label;
        li.querySelector('.meta').textContent = it.meta;
        li.querySelector('button').onclick = () => post('/api/central/open', { path: it.path });
        el.append(li);
      }
    };
    rows('#cf-big', r.grandes.map((b) => ({ label: short(b.caminho), meta: `${human(b.tamanho)} · modificado ${ago(b.mod)}`, path: b.caminho })),
      '#cf-big-h', `Arquivos grandes (${r.grandes.length})`);
    rows('#cf-dup', r.duplicados.map((d) => ({ label: d.arquivos.map(short).join('  ⇄  '), meta: `${d.arquivos.length} cópias de ${human(d.tamanho)}`, path: d.arquivos[0] })),
      '#cf-dup-h', `Duplicados (${r.duplicados_total} grupos · ${human(r.duplicados_espaco)} recuperáveis)`);
    rows('#cf-copy', r.copias.map((c) => ({ label: short(c), meta: 'nome de cópia ou genérico', path: c })),
      '#cf-copy-h', `Nomes de cópia ou genéricos (${r.copias_total})`);
  }
  function showAnalysis(a) {
    const box = $('#cf-ai-box');
    box.classList.remove('hidden');
    box.innerHTML = `<div class="ai-h">✦ Análise do MESTRE <span class="note">${new Date(a.quando * 1000).toLocaleString('pt-BR')}</span></div>${md(a.texto)}`;
  }
  $('#cf-scan').onclick = () => loadFiles(true);
  $('#cf-ai').onclick = async () => {
    const b = $('#cf-ai');
    b.disabled = true;
    b.textContent = '✦ Analisando (até 1 min)…';
    try {
      const a = await post('/api/central/files/analyze');
      if (a.error) throw new Error(a.error);
      showAnalysis(a);
      $('#cf-ai-box').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) { addLog('warn', `Análise falhou: ${e.message}`); }
    b.disabled = false;
    b.textContent = '✦ Análise completa pelo MESTRE';
  };

  // ---------------------------------------------------------- programas
  async function loadApps() {
    $('#ca-note').textContent = 'listando programas…';
    st.apps = await (await fetch('/api/central/apps')).json();
    st.ia = st.apps.ia || {};
    st.job = st.apps.job;
    renderApps();
  }
  const key = (a) => (st.src === 'apps' ? `app:${a.id}` : `pkg:${a.id}`);
  const catOf = (a) => (st.src === 'apps' ? a.categoria : a.secao?.split('/').pop() || 'outros');
  function visibleApps() {
    const q = st.q.toLowerCase();
    return (st.apps?.[st.src] || []).filter((a) => (st.cat === 'Todos' || catOf(a) === st.cat)
      && (!q || [a.nome, a.generico, a.descricao, a.palavras, st.ia[key(a)]?.faz, ...(st.ia[key(a)]?.capacidades || [])].join(' ').toLowerCase().includes(q)));
  }
  function renderApps() {
    if (!st.apps) return;
    const all = st.apps[st.src];
    const cats = ['Todos', ...[...new Set(all.map(catOf))].sort()];
    $('#ca-cats').innerHTML = '';
    for (const c of cats) {
      const b = document.createElement('button');
      b.innerHTML = `<span></span><i>${c === 'Todos' ? all.length : all.filter((a) => catOf(a) === c).length}</i>`;
      b.querySelector('span').textContent = c;
      b.classList.toggle('on', c === st.cat);
      b.onclick = () => { st.cat = c; renderApps(); };
      $('#ca-cats').append(b);
    }
    const list = visibleApps();
    const missing = list.filter((a) => !st.ia[key(a)]).length;
    $('#ca-explain').textContent = missing ? `✦ Explicar ${missing} com IA` : '✦ Todos explicados';
    $('#ca-explain').disabled = !missing || st.job?.rodando;
    $('#ca-note').textContent = st.job?.rodando ? `explicando ${st.job.feitos}/${st.job.total}…` : `${list.length} de ${all.length}`;
    const ul = $('#ca-list');
    ul.innerHTML = '';
    for (const a of list.slice(0, 400)) {
      const ex = st.ia[key(a)];
      const li = document.createElement('li');
      li.innerHTML = `<div class="ttl"></div><div class="meta"><b></b><span></span></div><div class="sum"></div>
        ${ex ? `<div class="explain"><p>${esc(ex.faz)}</p>${ex.capacidades?.length ? `<ul>${ex.capacidades.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>` : ''}${ex.dica ? `<p class="tip">💡 ${esc(ex.dica)}</p>` : ''}</div>` : ''}
        <div class="acts"></div>`;
      li.querySelector('.ttl').textContent = a.nome + (a.generico && a.generico !== a.nome ? ` — ${a.generico}` : '');
      li.querySelector('.meta b').textContent = catOf(a);
      li.querySelector('.meta span').textContent = st.src === 'apps' ? a.origem : `versão ${a.versao}`;
      li.querySelector('.sum').textContent = a.descricao || '';
      const acts = li.querySelector('.acts');
      if (st.src === 'apps') {
        const o = document.createElement('button');
        o.textContent = '▶ Abrir';
        o.onclick = () => post('/api/central/launch', { id: a.id });
        acts.append(o);
      }
      const e = document.createElement('button');
      e.textContent = ex ? '✦ Refazer' : '✦ Explicar';
      e.onclick = () => explain([a], !!ex);
      acts.append(e);
      const h = document.createElement('button');
      h.textContent = '? Perguntar';
      h.title = 'Perguntar ao MESTRE como tirar proveito deste programa';
      h.onclick = () => ask(`Me explique o programa "${a.nome}" instalado aqui (${a.descricao || a.id}): o que ele faz de melhor, como posso usá-lo no meu trabalho e se há algo parecido que eu já tenho instalado.`);
      acts.append(h);
      ul.append(li);
    }
  }
  async function explain(items, refazer = false) {
    const payload = items.map((a) => ({ key: key(a), nome: a.nome, descricao: a.descricao, tipo: st.src, categorias: a.categorias, secao: a.secao, exec: a.exec }));
    const r = await post('/api/central/apps/explain', { items: payload, refazer });
    if (!r.ok) addLog('warn', r.erro || 'Não foi possível explicar agora.');
  }
  $('#ca-explain').onclick = () => explain(visibleApps().filter((a) => !st.ia[key(a)]));
  $$('#ca-src button').forEach((b) => (b.onclick = () => {
    st.src = b.dataset.src;
    st.cat = 'Todos';
    $$('#ca-src button').forEach((x) => x.classList.toggle('on', x === b));
    renderApps();
  }));
  let qTimer;
  $('#ca-q').oninput = (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { st.q = e.target.value; renderApps(); }, 150); };

  // ---------------------------------------------------------- projetos e fluxos
  async function loadProjects(refresh = false) {
    $('#cp-note').textContent = 'procurando projetos…';
    const d = await (await fetch(`/api/central/projects${refresh ? '?refresh=true' : ''}`)).json();
    st.projects = d.projetos;
    $('#cp-note').textContent = `${d.projetos.length} encontrados · ${ago(d.quando)}`;
    renderProjects();
  }
  function renderProjects() {
    const box = $('#cp-list');
    box.innerHTML = '';
    for (const p of st.projects) {
      const c = document.createElement('div');
      c.className = 'proj' + (st.picked.has(p.caminho) ? ' picked' : '');
      const risk = (p.alteracoes ? `<span class="warn">${p.alteracoes} alterações</span>` : '')
        + (p.nao_enviados ? `<span class="warn">${p.nao_enviados} não enviados</span>` : '')
        + (p.tipos.includes('git') && p.remoto === false ? '<span class="warn">sem remoto</span>' : '');
      c.innerHTML = `<label class="pick"><input type="checkbox" ${st.picked.has(p.caminho) ? 'checked' : ''}></label>
        <div class="p-n">${esc(p.nome)}</div><div class="p-w">${esc(p.pai)}</div>
        <div class="p-t">${p.tipos.map((t) => `<i>${esc(t)}</i>`).join('')}${p.branch ? `<i class="br">⎇ ${esc(p.branch)}</i>` : ''}</div>
        <div class="p-r">${risk}</div>
        <div class="p-c">${p.ultimo_commit ? `${esc(p.ultimo_commit.msg)} · ${ago(p.ultimo_commit.quando)}` : `mexido ${ago(p.mod)}`}</div>
        <div class="p-a"><button data-a="open">📂</button><button data-a="status">✦ Estado e próximos passos</button></div>`;
      c.querySelector('input').onchange = (e) => {
        e.target.checked ? st.picked.add(p.caminho) : st.picked.delete(p.caminho);
        c.classList.toggle('picked', e.target.checked);
        $('#fl-new-paths').textContent = st.picked.size ? `Incluídos: ${[...st.picked].map(short).join(', ')}` : 'Marque projetos acima para incluí-los no fluxo.';
      };
      c.querySelector('[data-a=open]').onclick = () => post('/api/central/open', { path: p.caminho });
      c.querySelector('[data-a=status]').onclick = () => ask(`Analise o projeto em ${p.caminho}: leia o README e o histórico recente${p.tipos.includes('git') ? ' do git' : ''}, diga em que estado ele está, riscos (trabalho sem backup, alterações pendentes) e os 3 próximos passos mais úteis.`);
      box.append(c);
    }
  }
  $('#cp-scan').onclick = () => loadProjects(true);

  const REPEAT = { diario: 'todo dia', 'dias-uteis': 'dias úteis', semanal: 'toda semana' };
  async function loadFlows() {
    st.flows = await (await fetch('/api/central/flows')).json();
    renderFlows();
  }
  function renderFlows() {
    const ul = $('#fl-list');
    ul.innerHTML = '';
    for (const f of st.flows) {
      const li = document.createElement('li');
      li.dataset.id = f.id;
      const ag = f.agendado;
      li.innerHTML = `<div class="ttl"><span class="ico">${esc(f.icone || '⚡')}</span> <span class="n"></span>${f.modelo ? '' : ' <span class="lvl baixa">meu</span>'}</div>
        <div class="sum"></div>
        <div class="meta">${ag ? `<b>⏱ ${REPEAT[ag.repeat] || 'agendado'}</b>próximo: ${new Date(ag.when * 1000).toLocaleString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}` : '<span>não agendado</span>'}${f.pastas?.length ? ` · ${f.pastas.map(short).join(', ')}` : ''}</div>
        <div class="acts">
          <button data-a="run" title="Executa agora, na conversa (você confirma as ações)">▶ Executar agora</button>
          <button data-a="bg" title="Executa sozinho; ações protegidas ficam pendentes no relatório">⧉ Em segundo plano</button>
          <span class="sched"><select><option value="">Não agendar</option><option value="diario">Todo dia</option><option value="dias-uteis">Dias úteis</option><option value="semanal">Toda semana</option></select><input type="time" value="08:00"><button data-a="sch" title="Salvar agendamento">⏱ Agendar</button></span>
          ${f.modelo ? '' : '<button data-a="del" class="del">Excluir</button>'}
        </div>`;
      li.querySelector('.n').textContent = f.nome;
      li.querySelector('.sum').textContent = f.descricao || f.pedido;
      li.querySelector('.sum').title = f.pedido;
      const sel = li.querySelector('select');
      sel.value = ag?.repeat || f.repetir || '';
      if (ag) li.querySelector('input[type=time]').value = new Date(ag.when * 1000).toTimeString().slice(0, 5);
      li.querySelector('[data-a=run]').onclick = () => { post(`/api/central/flows/${f.id}/run`, { modo: 'conversa' }); $('#central').classList.add('hidden'); stopSpeech(); };
      li.querySelector('[data-a=bg]').onclick = async () => { await post(`/api/central/flows/${f.id}/run`, { modo: 'segundo-plano' }); addLog('task', `▶ Fluxo em segundo plano: ${f.nome}`); };
      li.querySelector('[data-a=sch]').onclick = async () => {
        await post(`/api/central/flows/${f.id}/schedule`, { repetir: sel.value, hora: li.querySelector('input[type=time]').value });
        addLog('ok', sel.value ? `⏱ Fluxo "${f.nome}" agendado: ${REPEAT[sel.value]}` : `Fluxo "${f.nome}" sem agendamento`);
        loadFlows();
      };
      li.querySelector('[data-a=del]')?.addEventListener('click', async () => {
        await fetch(`/api/central/flows/${f.id}`, { method: 'DELETE' });
        loadFlows();
      });
      ul.append(li);
    }
  }
  $('#fl-new').onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = { nome: fd.get('nome'), descricao: fd.get('descricao'), pedido: fd.get('pedido'), pastas: [...st.picked], repetir: fd.get('repetir') || null };
    const f = await post('/api/central/flows', body);
    if (f.error) return addLog('warn', f.error);
    if (body.repetir) await post(`/api/central/flows/${f.id}/schedule`, { repetir: body.repetir, hora: '08:00' });
    e.target.reset();
    $('#fl-new-box').open = false;
    addLog('ok', `Fluxo criado: ${f.nome}`);
    loadFlows();
  };
  $('#fl-suggest').onclick = async () => {
    const b = $('#fl-suggest');
    b.disabled = true;
    b.textContent = '✦ Pensando nos seus fluxos (até 1 min)…';
    const ul = $('#fl-sugg');
    try {
      const list = await post('/api/central/flows/suggest');
      if (list.error) throw new Error(list.error);
      ul.innerHTML = '';
      for (const s of list) {
        const li = document.createElement('li');
        li.className = 'suggested';
        li.innerHTML = `<div class="ttl"><span class="ico"></span> <span class="n"></span> <span class="lvl media">sugerido</span></div><div class="sum"></div><div class="meta"></div>
          <div class="acts"><button data-a="add">+ Adicionar</button><button data-a="no">Descartar</button></div>`;
        li.querySelector('.ico').textContent = s.icone || '⚡';
        li.querySelector('.n').textContent = s.nome;
        li.querySelector('.sum').textContent = s.descricao;
        li.querySelector('.meta').textContent = `${s.repetir ? `sugestão: ${REPEAT[s.repetir] || s.repetir} · ` : ''}${s.pedido}`;
        li.querySelector('[data-a=add]').onclick = async () => {
          const f = await post('/api/central/flows', s);
          if (s.repetir && REPEAT[s.repetir]) await post(`/api/central/flows/${f.id}/schedule`, { repetir: s.repetir, hora: '08:00' });
          li.remove();
          loadFlows();
        };
        li.querySelector('[data-a=no]').onclick = () => li.remove();
        ul.append(li);
      }
    } catch (e) { addLog('warn', `Sugestão falhou: ${e.message}`); }
    b.disabled = false;
    b.textContent = '✦ Sugerir fluxos para mim';
  };

  return {
    onMessage(m) {
      if (m.what === 'apps') {
        st.job = m.job;
        Object.assign(st.ia, m.ia || {});
        if (st.apps) renderApps();
        if (!m.job.rodando && m.job.erro) addLog('warn', `Explicação parcial: ${m.job.erro}`);
      }
    },
    onAgenda() {
      if (!$('#central').classList.contains('hidden') && st.flows.length) loadFlows();
    },
  };
}
