import { createFace } from './symbol.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const face = createFace($('#face'));
window.__mestreFace = face; // inspeção/testes

const STATE_LABEL = {
  boot: 'Inicializando', idle: 'Em espera', listening: 'Ouvindo', transcribing: 'Processando fala',
  thinking: 'Pensando', acting: 'Executando', speaking: 'Falando', pending: 'Aguardando confirmação',
  offline: 'Sem conexão',
};
const THEMES = {
  ciano: [0, 229, 255], verde: [0, 255, 163], ambar: [255, 179, 0], vermelho: [255, 61, 87],
  roxo: [179, 136, 255], rosa: [255, 95, 210], branco: [232, 244, 255],
};
const baseColor = () => THEMES[settings.theme] || COLORS[settings.gender];
const COLORS = {
  masculino: [0, 229, 255], feminino: [255, 95, 210], listening: [0, 255, 163], transcribing: [0, 255, 163],
  thinking: [255, 179, 0], acting: [179, 136, 255], pending: [255, 61, 87], offline: [90, 110, 120],
};
let settings = { mode: 'free', gender: 'masculino', stt: 'local', model: 'opus', watch: true, always: [] };
let uiState = 'boot';

function setState(s) {
  uiState = s;
  document.body.dataset.state = s;
  $('#status').textContent = STATE_LABEL[s] || s;
  face.setColor((s !== 'masculino' && s !== 'feminino' && COLORS[s]) || baseColor());
  face.setEnergy(s === 'thinking' || s === 'acting' ? 0.6 : s === 'listening' ? 0.4 : 0);
  if (s === 'listening') face.setExpression('ouvindo');
  else if (s === 'thinking') face.setExpression('pensativo');
  else if (s === 'pending') face.setExpression('alerta');
  if (s !== 'acting' && s !== 'thinking') $('#action').textContent = '';
}

// ------------------------------------------------------------ registro lateral
const log = $('#log');
let liveReply = null;
function addLog(kind, text) {
  const li = document.createElement('li');
  li.className = kind;
  li.innerHTML = `<time>${new Date().toLocaleTimeString('pt-BR')}</time>`;
  const span = document.createElement('span');
  span.textContent = text;
  li.append(span);
  log.append(li);
  while (log.children.length > 150) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
  return span;
}

// ------------------------------------------------------------ sons de interface
function beep(freq = 880, dur = 0.08, vol = 0.05) {
  if (!ctx) return;
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.frequency.value = freq;
  o.type = 'sine';
  g.gain.setValueAtTime(vol, ctx.currentTime);
  g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
  o.connect(g).connect(ctx.destination);
  o.start();
  o.stop(ctx.currentTime + dur);
}

// ------------------------------------------------------------ áudio de saída
let ctx;
let outAnalyser;
let micAnalyser;
const playQueue = [];
let playing = null;
let speechDone = true;
let quietUntil = 0;

async function ensureAudio() {
  if (ctx) return;
  ctx = new AudioContext();
  outAnalyser = ctx.createAnalyser();
  outAnalyser.fftSize = 512;
  outAnalyser.connect(ctx.destination);
}
let relaxTimer = null;
async function enqueueSpeech(b64, emotion, meta) {
  await ensureAudio();
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const buf = await ctx.decodeAudioData(bytes.buffer);
  buf.emotion = emotion;
  buf.meta = meta;
  if (meta && meta.gen !== reading.gen) return; // trecho de uma leitura já parada
  playQueue.push(buf);
  if (!playing) playNext();
}
function playNext() {
  const buf = playQueue.shift();
  if (!buf) {
    playing = null;
    if (reading.active && reading.ended) finishReading();
    quietUntil = performance.now() + 600; // não ouve o eco do fim da própria fala
    if (speechDone && uiState === 'speaking') setState('idle');
    // A expressão da última frase fica um pouco no rosto e depois relaxa.
    clearTimeout(relaxTimer);
    relaxTimer = setTimeout(() => { if (!playing && uiState === 'idle') face.setExpression('neutro'); }, 2500);
    return;
  }
  clearTimeout(relaxTimer);
  if (buf.emotion) face.setExpression(buf.emotion);
  if (buf.meta) onReaderChunk(buf.meta);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(outAnalyser);
  src.onended = playNext;
  playing = src;
  if (uiState !== 'pending') setState('speaking');
  src.start();
}
function stopSpeech() {
  if (reading.active) stopReading(true);
  playQueue.length = 0;
  if (playing) {
    playing.onended = null;
    try { playing.stop(); } catch {}
    playing = null;
  }
}
const isSpeaking = () => Boolean(playing);

// Boca e onda circular seguem o volume.
const wave = $('#wave');
const wctx = wave.getContext('2d');
const tdata = new Uint8Array(512);
const fdata = new Uint8Array(256);
function level(an) {
  if (!an) return 0;
  an.getByteTimeDomainData(tdata);
  let s = 0;
  for (let i = 0; i < tdata.length; i++) s += ((tdata[i] - 128) / 128) ** 2;
  return Math.sqrt(s / tdata.length);
}
function drawWave() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const r = wave.getBoundingClientRect();
  if (wave.width !== Math.round(r.width * dpr)) {
    wave.width = Math.round(r.width * dpr);
    wave.height = Math.round(r.height * dpr);
  }
  const W = wave.width;
  const H = wave.height;
  wctx.clearRect(0, 0, W, H);
  face.setMouth(isSpeaking() ? Math.min(1, level(outAnalyser) * 5.5) : 0);
  const an = isSpeaking() ? outAnalyser : recording ? micAnalyser : null;
  if (an) an.getByteFrequencyData(fdata);
  const c = getComputedStyle(document.body).getPropertyValue('--c').trim() || '#00e5ff';
  const R = W * 0.335;
  wctx.strokeStyle = c;
  wctx.shadowColor = c;
  wctx.shadowBlur = 12 * dpr;
  wctx.lineWidth = 2 * dpr;
  const N = 110;
  for (let i = 0; i < N; i++) {
    const v = an ? fdata[Math.floor((i % (N / 2)) * 1.3) + 2] / 255 : 0.04 + 0.03 * Math.sin(performance.now() / 600 + i);
    const a = (i / N) * Math.PI * 2 - Math.PI / 2;
    const len = 6 * dpr + v * W * 0.09;
    wctx.globalAlpha = 0.25 + v * 0.75;
    wctx.beginPath();
    wctx.moveTo(W / 2 + Math.cos(a) * R, H / 2 + Math.sin(a) * R);
    wctx.lineTo(W / 2 + Math.cos(a) * (R + len), H / 2 + Math.sin(a) * (R + len));
    wctx.stroke();
  }
  wctx.globalAlpha = 1;
  requestAnimationFrame(drawWave);
}
requestAnimationFrame(drawWave);

// ------------------------------------------------------------ chuva de números
const rain = $('#rain');
const rctx = rain.getContext('2d');
const GLYPHS = '0123456789ABCDEF01アイウエオカキクケコサシスセソ0110<>/{}#$%=+';
let drops = [];
function sizeRain() {
  rain.width = innerWidth;
  rain.height = innerHeight;
  drops = Array.from({ length: Math.ceil(innerWidth / 16) }, () => ({ y: Math.random() * -60, v: 0.25 + Math.random() * 0.55 }));
}
sizeRain();
addEventListener('resize', sizeRain);
let lastRain = 0;
function drawRain(now) {
  if (now - lastRain > 50) {
    lastRain = now;
    rctx.fillStyle = 'rgba(2, 6, 12, 0.13)';
    rctx.fillRect(0, 0, rain.width, rain.height);
    const c = getComputedStyle(document.body).getPropertyValue('--c').trim() || '#00e5ff';
    rctx.font = '15px "DejaVu Sans Mono", monospace';
    const busy = uiState === 'thinking' || uiState === 'acting';
    drops.forEach((d, i) => {
      const ch = GLYPHS[(Math.random() * GLYPHS.length) | 0];
      const y = d.y * 18;
      rctx.fillStyle = '#e8fbff';
      rctx.globalAlpha = 0.55;
      rctx.fillText(ch, i * 16, y);
      rctx.fillStyle = c;
      rctx.globalAlpha = 0.35;
      rctx.fillText(GLYPHS[(Math.random() * GLYPHS.length) | 0], i * 16, y - 18);
      d.y += d.v * (busy ? 2.2 : 1);
      if (y > rain.height && Math.random() > 0.975) d.y = Math.random() * -20;
    });
    rctx.globalAlpha = 1;
  }
  requestAnimationFrame(drawRain);
}
requestAnimationFrame(drawRain);

// ------------------------------------------------------------ conexão
let ws;
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { if (uiState === 'offline' || uiState === 'boot') setState('idle'); };
  ws.onclose = () => { setState('offline'); setTimeout(connect, 1500); };
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
}
const send = (o) => ws?.readyState === 1 && ws.send(JSON.stringify(o));

function onMessage(m) {
  switch (m.type) {
    case 'hello':
      applySettings(m.settings);
      renderAgenda(m.agenda || []);
      break;
    case 'settings':
      applySettings(m.settings);
      break;
    case 'status':
      if (m.state === 'idle' && (isSpeaking() || !speechDone)) break;
      if (uiState === 'pending' && m.state !== 'thinking') break;
      setState(m.state);
      break;
    case 'heard':
      addLog('ignored', `${m.text} (sem “Mestre”)`);
      if (!isSpeaking()) setState('idle');
      break;
    case 'user':
      liveReply = null;
      speechDone = false;
      stopSpeech();
      addLog('user', m.text);
      setState('thinking');
      break;
    case 'delta':
      if (!liveReply) liveReply = addLog('mestre', '');
      liveReply.textContent += m.text.replace(/[*_#`]/g, '');
      log.scrollTop = log.scrollHeight;
      break;
    case 'emotion':
      face.setExpression(m.name);
      break;
    case 'say':
      enqueueSpeech(m.audio, m.emotion);
      break;
    case 'reader_start':
      Object.assign(reading, { active: true, ended: false, paused: false, gen: (reading.gen || 0) + 0, title: m.title, total: m.total, kind: m.kind, doc: m.doc_id, index: m.index });
      showPlayer();
      break;
    case 'reader_say':
      reading.gen = m.gen;
      reading.total = m.total;
      enqueueSpeech(m.audio, null, { gen: m.gen, index: m.index, text: m.text, page: m.page, total: m.total });
      break;
    case 'reader_end_queued':
      reading.ended = true;
      if (!playing && !playQueue.length) finishReading();
      break;
    case 'library':
      renderLibrary(m.items);
      break;
    case 'tool':
      liveReply = null;
      addLog('tool', `⚙ ${m.detail || m.name}`);
      $('#action').textContent = `⚙ ${m.detail || m.name}`;
      if (uiState !== 'speaking' && uiState !== 'pending') setState('acting');
      break;
    case 'pending':
      $('#confirm-why').textContent = `O MESTRE quer ${m.why}.`;
      $('#confirm-what').textContent = m.summary;
      $('#confirm').classList.remove('hidden');
      addLog('warn', `Protegido: ${m.why}`);
      beep(520, 0.15, 0.06);
      setState('pending');
      break;
    case 'approved':
      $('#confirm').classList.add('hidden');
      addLog('ok', `${m.always ? 'Permitido sempre' : 'Confirmado'}: ${m.summary}`);
      setState('thinking');
      break;
    case 'denied':
      $('#confirm').classList.add('hidden');
      addLog('warn', `Negado: ${m.summary}`);
      setState('thinking');
      break;
    case 'done':
      speechDone = true;
      liveReply = null;
      if (m.error && m.exited) addLog('warn', 'O núcleo reiniciou');
      if (!isSpeaking() && !playQueue.length && uiState !== 'pending') setState('idle');
      break;
    case 'task': {
      const j = m.job;
      addLog('task', j.status === 'rodando' ? `▶ Tarefa em segundo plano: ${j.text}` : `■ Tarefa ${j.status}: ${j.result || j.text}`);
      break;
    }
    case 'reminder':
      addLog('task', `⏰ Lembrete: ${m.item.text}`);
      beep(988, 0.12, 0.07);
      setTimeout(() => beep(1318, 0.18, 0.07), 140);
      break;
    case 'alert':
      addLog('warn', `⚠ Alerta: ${m.text}`);
      break;
    case 'agenda':
      renderAgenda(m.items);
      break;
    case 'reset':
      log.innerHTML = '';
      addLog('ok', 'Nova conversa iniciada.');
      break;
  }
}

// ------------------------------------------------------------ microfone
let recording = false;
let pttHeld = false;
let micReady = false;
let chunks = [];
let preRoll = [];
let voiced = 0;
let silence = 0;
let noise = 0.008;
let speech = 0; // blocos de 20 ms com voz dentro da frase atual

async function startMic() {
  await ensureAudio();
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });
  const src = ctx.createMediaStreamSource(stream);
  micAnalyser = ctx.createAnalyser();
  micAnalyser.fftSize = 512;
  src.connect(micAnalyser);
  const code = `class Tap extends AudioWorkletProcessor{process(i){if(i[0]&&i[0][0])this.port.postMessage(i[0][0].slice(0));return true}}registerProcessor('tap',Tap)`;
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([code], { type: 'text/javascript' })));
  const node = new AudioWorkletNode(ctx, 'tap');
  src.connect(node);
  let frame = [];
  node.port.onmessage = (e) => {
    frame.push(e.data);
    if (frame.length * 128 < ctx.sampleRate * 0.02) return;
    const block = concat(frame);
    frame = [];
    onBlock(block);
  };
  micReady = true;
}
function concat(list) {
  const n = list.reduce((a, b) => a + b.length, 0);
  const out = new Float32Array(n);
  let o = 0;
  for (const a of list) { out.set(a, o); o += a.length; }
  return out;
}
function rms(a) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * a[i];
  return Math.sqrt(s / a.length);
}

function onBlock(block) {
  // Espaço apertado: grava tudo, sem limite de tempo, em qualquer modo.
  if (pttHeld) { chunks.push(block); return; }
  if (settings.mode === 'ptt') return;
  const lv = rms(block);
  // Mãos livres: não escuta enquanto fala (evita ouvir a própria voz).
  if (isSpeaking() || performance.now() < quietUntil || !$('#confirm').classList.contains('hidden')) {
    if (recording) endUtterance(false);
    return;
  }
  if (!recording) {
    noise = noise * 0.97 + Math.min(lv, 0.05) * 0.03;
    preRoll.push(block);
    if (preRoll.length > 15) preRoll.shift();
    voiced = lv > Math.max(0.02, noise * 3.5) ? voiced + 1 : 0;
    if (voiced >= 5) {
      recording = true;
      chunks = [...preRoll];
      silence = 0;
      speech = voiced;
      setState('listening');
    }
  } else {
    chunks.push(block);
    if (lv > Math.max(0.02, noise * 3.5)) speech += 1;
    silence = lv < Math.max(0.013, noise * 2.4) ? silence + 1 : 0;
    const seconds = (chunks.length * block.length) / ctx.sampleRate;
    // Mãos livres: 0,9 s de silêncio encerra a frase; no máximo 30 s.
    if (silence > 45 || seconds > 30) endUtterance(true);
  }
}

function endUtterance(sendIt) {
  recording = false;
  voiced = 0;
  const pcm = concat(chunks);
  chunks = [];
  preRoll = [];
  // Mãos livres: menos de ~0,4 s de voz de verdade é ruído (porta, tosse, clique).
  const tooLittle = !pttHeldLast && speech < 20;
  speech = 0;
  if (!sendIt || pcm.length < ctx.sampleRate * 0.4 || tooLittle) {
    if (uiState === 'listening') setState('idle');
    return;
  }
  beep(660, 0.06, 0.035);
  ws?.send(to16k(pcm));
}

function to16k(f32) {
  const ratio = ctx.sampleRate / 16000;
  const n = Math.floor(f32.length / ratio);
  const out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    // média simples como filtro passa-baixa antes de reduzir a taxa
    let s = 0;
    const a = Math.floor(i * ratio);
    const b = Math.max(a + 1, Math.floor((i + 1) * ratio));
    for (let k = a; k < b && k < f32.length; k++) s += f32[k];
    out[i] = Math.max(-1, Math.min(1, s / (b - a))) * 0x7fff;
  }
  return out.buffer;
}

function pttDown() {
  if (!micReady || pttHeld) return;
  stopSpeech();
  send({ type: 'stop' });
  pttHeld = true;
  recording = true;
  chunks = [];
  $('#mic').classList.add('rec');
  beep(880, 0.07, 0.04);
  setState('listening');
}
let pttHeldLast = false;
function pttUp() {
  if (!pttHeld) return;
  pttHeld = false;
  pttHeldLast = true;
  setTimeout(() => (pttHeldLast = false), 50);
  $('#mic').classList.remove('rec');
  endUtterance(true);
}
$('#mic').addEventListener('pointerdown', pttDown);

// Símbolo central: esquerdo segura para falar, direito abre as configurações,
// rodinha liga/desliga a animação.
let animOff = false;
function setAnimation(off) {
  animOff = off;
  face.setPaused(off);
  document.body.classList.toggle('anim-off', off);
  try { localStorage.setItem('mestre-anim-off', off ? '1' : '0'); } catch {}
}
try { if (localStorage.getItem('mestre-anim-off') === '1') setAnimation(true); } catch {}
const faceEl = $('#face');
faceEl.addEventListener('pointerdown', (e) => {
  if (e.button === 0) { e.preventDefault(); pttDown(); }
  else if (e.button === 1) { e.preventDefault(); setAnimation(!animOff); }
});
faceEl.addEventListener('mousedown', (e) => { if (e.button === 1) e.preventDefault(); }); // sem auto-rolagem do Chrome
faceEl.addEventListener('auxclick', (e) => e.preventDefault());
faceEl.addEventListener('contextmenu', (e) => {
  e.preventDefault();
  pttUp();
  $('#settings').classList.remove('hidden');
  loadConnections();
});
window.addEventListener('pointerup', pttUp);
const typing = () => document.activeElement?.tagName === 'INPUT';
window.addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !typing() && !e.repeat) { e.preventDefault(); pttDown(); }
  if (e.code === 'Escape') {
    if (!$('#settings').classList.contains('hidden')) return $('#settings').classList.add('hidden');
    if (!$('#reader').classList.contains('hidden')) return $('#reader').classList.add('hidden');
    stopSpeech();
    send({ type: 'stop' });
    setState('idle');
  }
});
window.addEventListener('keyup', (e) => { if (e.code === 'Space' && !typing()) { e.preventDefault(); pttUp(); } });

// ------------------------------------------------------------ ajustes
const HINTS = {
  free: 'Fale normalmente: o MESTRE ouve e responde a tudo. Segure <b>espaço</b> para falar sem limite de tempo.',
  wake: 'Diga <b>“Mestre”</b> na frase. Depois de cada resposta, pode continuar sem o nome por alguns segundos.',
  ptt: 'Segure a <b>barra de espaço</b> (ou o microfone) enquanto fala, sem limite de tempo.',
};
function applySettings(s) {
  settings = { ...settings, ...s };
  document.body.dataset.gender = settings.gender;
  document.body.dataset.theme = settings.theme || 'auto';
  document.body.classList.toggle('norain', settings.rain === false);
  face.setGender(settings.gender);
  if (!['thinking', 'acting', 'listening', 'pending'].includes(uiState)) face.setColor(baseColor());
  $$('#theme button').forEach((b) => b.classList.toggle('on', b.dataset.v === (settings.theme || 'auto')));
  $$('.modes button').forEach((b) => b.classList.toggle('on', b.dataset.mode === settings.mode));
  $('#hint').innerHTML = HINTS[settings.mode];
  $$('.seg').forEach((seg) => {
    const v = String(settings[seg.dataset.key]);
    seg.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === v));
  });
  const sel = $('#voice');
  sel.innerHTML = '';
  for (const [v, label] of Object.entries(settings.voice_choices || {})) {
    const o = document.createElement('option');
    o.value = v;
    o.textContent = label;
    o.selected = v === settings.voice;
    sel.append(o);
  }
  $('#pitch').value = settings.pitch ?? 1;
  $('#pitch-v').textContent = Number(settings.pitch ?? 1).toFixed(2);
  renderPerms();
  if (document.activeElement !== $('#mem-dir')) $('#mem-dir').value = settings.memory_dir || '';
  const vaultOn = (settings.permissions || []).find((p) => p.key === 'vault')?.on !== false;
  $('#mem-state').textContent = !settings.memory_dir
    ? 'Nenhuma pasta escolhida: o MESTRE não guarda memória em arquivos.'
    : settings.memory_ok
      ? `✔ ${settings.memory_notes} notas .md nesta pasta${vaultOn ? '' : ' · a permissão "Memória no Obsidian" está desligada'}. Ele cria o índice, a nota sobre você e o diário se faltarem.`
      : '⚠ Pasta não encontrada.';
  const showTicker = settings.ticker !== false && settings.newsflash;
  $('#ticker').classList.toggle('hidden', !showTicker);
  if (showTicker && !tickerLoaded) loadTicker();
  $('#key-state').textContent = settings.openai_key_set ? 'Chave da OpenAI salva (só neste PC).' : 'Nenhuma chave da OpenAI salva.';
  const al = $('#always');
  al.innerHTML = '';
  if (!settings.always?.length) al.innerHTML = '<li class="empty">Nenhuma ação permitida para sempre.</li>';
  for (const r of settings.always || []) {
    const li = document.createElement('li');
    li.innerHTML = `<div><code></code><div class="note"></div></div><button>Revogar</button>`;
    li.querySelector('code').textContent = r.match;
    li.querySelector('.note').textContent = r.why;
    li.querySelector('button').onclick = () => postSettings({ remove_always: r.match });
    al.append(li);
  }
}
// ------------------------------------------------------------ conexões e permissões
let connStatus = {};
const BADGE = { conectado: 'ok', 'precisa entrar': 'need', falhou: 'bad', desconhecido: 'unk' };
function renderPerms() {
  const box = $('#perms');
  if (!box || !settings.permissions) return;
  box.innerHTML = '';
  let group = '';
  for (const p of settings.permissions) {
    if (p.group !== group) {
      group = p.group;
      const h = document.createElement('div');
      h.className = 'perm-group';
      h.textContent = group;
      box.append(h);
    }
    const row = document.createElement('div');
    row.className = 'perm';
    const status = connStatus[p.key];
    row.innerHTML = `<b></b><small></small><div class="side"></div>`;
    row.querySelector('b').textContent = p.label;
    row.querySelector('small').textContent = p.desc;
    const side = row.querySelector('.side');
    if (status) {
      const badge = document.createElement('span');
      badge.className = `badge ${BADGE[status] || 'unk'}`;
      badge.textContent = status;
      side.append(badge);
      if (status === 'precisa entrar' || status === 'falhou') {
        const btn = document.createElement('button');
        btn.className = 'connect';
        btn.textContent = 'Conectar';
        btn.onclick = () => { fetch(`/api/connect/${p.key}`, { method: 'POST' }); $('#settings').classList.add('hidden'); };
        side.append(btn);
      }
    }
    const sw = document.createElement('label');
    sw.className = 'switch';
    sw.innerHTML = `<input type="checkbox"><span></span>`;
    const input = sw.querySelector('input');
    input.checked = p.on;
    input.onchange = () => postSettings({ permissions: { [p.key]: input.checked } });
    side.append(sw);
    box.append(row);
  }
}
async function loadConnections(refresh = false) {
  try {
    connStatus = await (await fetch(`/api/connections${refresh ? '?refresh=true' : ''}`)).json();
    renderPerms();
  } catch {}
}
$('#conn-refresh').onclick = () => { $('#conn-refresh').textContent = 'Verificando…'; loadConnections(true).then(() => ($('#conn-refresh').textContent = 'Atualizar status')); };
$('#conn-manage').onclick = () => fetch('/api/connect/manage', { method: 'POST' });

async function postSettings(data) {
  const r = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  applySettings(await r.json());
}
$$('.modes button').forEach((b) => (b.onclick = () => postSettings({ mode: b.dataset.mode })));
$$('.seg button').forEach((b) => (b.onclick = () => {
  const key = b.parentElement.dataset.key;
  const v = ['watch', 'rain', 'ticker'].includes(key) ? b.dataset.v === 'true' : b.dataset.v;
  if (key === 'stt' && v === 'openai' && !settings.openai_key_set) $('#openai-key').focus();
  postSettings({ [key]: v });
}));
$('#voice').onchange = (e) => postSettings({ voice: e.target.value });
$('#pitch').oninput = (e) => ($('#pitch-v').textContent = Number(e.target.value).toFixed(2));
$('#pitch').onchange = (e) => postSettings({ pitch: Number(e.target.value) });
$('#test-voice').onclick = () => send({ type: 'test_voice' });
$('#mem-pick').onclick = async () => {
  $('#mem-state').textContent = 'Escolha a pasta na janela que abriu…';
  const { path } = await (await fetch('/api/memory/pick', { method: 'POST' })).json();
  if (path) { $('#mem-dir').value = path; saveMemoryDir(); } else applySettings(settings);
};
async function saveMemoryDir() {
  const r = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ memory_dir: $('#mem-dir').value }) });
  const d = await r.json();
  applySettings(d);
  if (d.error) $('#mem-state').textContent = `✖ ${d.error}`;
  else addLog('ok', `Memória: ${d.memory_dir || 'desativada'}`);
}
$('#mem-save').onclick = saveMemoryDir;
$('#mem-dir').onkeydown = (e) => { if (e.key === 'Enter') saveMemoryDir(); };
$('#mem-open').onclick = () => fetch('/api/memory/open', { method: 'POST' });
$('#save-key').onclick = () => { postSettings({ openai_api_key: $('#openai-key').value }); $('#openai-key').value = ''; };

function renderAgenda(items) {
  $('#agenda-n').textContent = `${items.length} itens`;
  const ul = $('#agenda');
  ul.innerHTML = '';
  if (!items.length) ul.innerHTML = '<li class="empty">Nada agendado. Peça: “me lembre às 15h de…”.</li>';
  for (const i of items) {
    const li = document.createElement('li');
    const when = new Date(i.when * 1000).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    li.innerHTML = `<div><code></code><div class="note"></div></div><button>Remover</button>`;
    li.querySelector('code').textContent = `${when} · ${i.kind}${i.repeat ? ` · ${i.repeat}` : ''}`;
    li.querySelector('.note').textContent = i.text;
    li.querySelector('button').onclick = () => fetch(`/api/agenda/${i.id}`, { method: 'DELETE' });
    ul.append(li);
  }
}

// confirmação
$$('#confirm .choices button').forEach((b) => (b.onclick = () => {
  send({ type: 'decision', choice: b.dataset.choice });
  $('#confirm').classList.add('hidden');
}));

// janela
const win = (a) => fetch(`/api/window/${a}`, { method: 'POST' });
$('#w-min').onclick = () => win('minimize');
$('#w-max').onclick = () => win('maximize');
$('#w-close').onclick = () => { stopSpeech(); win('close'); };
$('#w-off').onclick = () => { stopSpeech(); win('off'); };
const openSettings = () => {
  $('#settings').classList.toggle('hidden');
  if (!$('#settings').classList.contains('hidden')) loadConnections();
};
$('#w-settings').onclick = openSettings;
$('#open-settings').onclick = openSettings;
$$('#theme button').forEach((b) => (b.onclick = () => postSettings({ theme: b.dataset.v })));
$('#settings-close').onclick = () => $('#settings').classList.add('hidden');

// controles
$('#stop').onclick = () => { stopSpeech(); send({ type: 'stop' }); setState('idle'); };
$('#new').onclick = () => { stopSpeech(); send({ type: 'reset' }); };
$$('.quick button').forEach((b) => (b.onclick = () => { stopSpeech(); send({ type: 'text', text: b.dataset.q }); }));
$('#typed').onsubmit = (e) => {
  e.preventDefault();
  const t = $('#text').value.trim();
  if (!t) return;
  $('#text').value = '';
  $('#text').blur();
  stopSpeech();
  send({ type: 'text', text: t });
};

// ------------------------------------------------------------ faixa de notícias (NewsFlash)
let tickerLoaded = false;
async function loadTicker(refresh = false) {
  tickerLoaded = true;
  try {
    const data = await (await fetch(`/api/news${refresh ? '?refresh=true' : ''}`)).json();
    const track = $('#tk-track');
    if (!data.items?.length) { $('#ticker').classList.add('hidden'); return; }
    track.innerHTML = '';
    // duas cópias seguidas: a animação anda metade e recomeça sem salto
    for (let copy = 0; copy < 2; copy++) {
      for (const it of data.items) {
        const el = document.createElement('span');
        el.className = 'tk-item';
        el.innerHTML = '<b></b><span></span><i></i>';
        el.querySelector('b').textContent = it.categoria || it.fonte;
        el.querySelector('span').textContent = it.titulo;
        el.querySelector('i').textContent = it.quando;
        el.title = `${it.fonte} — clique para o MESTRE ler o resumo`;
        el.onclick = () => {
          stopSpeech();
          send({ type: 'text', text: `Leia e me resuma em três frases esta notícia do ${it.fonte}: "${it.titulo}" (${it.link}). Use mestre-noticias ler.` });
        };
        track.append(el);
      }
    }
    track.style.animationDuration = `${Math.max(60, data.items.length * 9)}s`;
  } catch {}
}
setInterval(() => { if (settings.ticker !== false && settings.newsflash) loadTicker(true); }, 10 * 60 * 1000);

// ------------------------------------------------------------ leitor: reprodução
const reading = { active: false, paused: false, ended: false, gen: -1, index: 0, total: 0, title: '', kind: null, doc: null, show: true };
const queuedReaderChunks = () => playQueue.filter((b) => b.meta).length + (playing && playing.buffer?.meta ? 1 : 0);
function onReaderChunk(meta) {
  reading.index = meta.index;
  reading.total = meta.total;
  send({ type: 'reader_pos', gen: meta.gen, index: meta.index });
  updatePlayer(meta);
  // pede mais trechos para manter a fila sempre um pouco à frente
  if (queuedReaderChunks() <= 2) send({ type: 'reader_need', gen: meta.gen });
}
function syncModalPlayer() {
  $('#modal-player').classList.toggle('hidden', $('#player').classList.contains('hidden'));
  $('#mp-pos').textContent = `${reading.title} · ${$('#pl-pos').textContent}`;
  $('.mp-toggle').textContent = $('#pl-toggle').textContent === '⏸' ? '⏸ Pausar' : '▶ Continuar';
}
setInterval(syncModalPlayer, 400);
function showPlayer() {
  $('#player').classList.remove('hidden');
  $('#pl-title').textContent = reading.title;
  $('#pl-toggle').textContent = '⏸';
  $('#pl-pos').textContent = 'preparando…';
}
function updatePlayer(meta) {
  const pct = reading.total ? ((meta.index + 1) / reading.total) * 100 : 0;
  $('#pl-fill').style.width = `${pct}%`;
  $('#pl-pos').textContent = `${meta.page ? `pág. ${meta.page} · ` : ''}${meta.index + 1}/${reading.total} · ${Math.round(pct)}%`;
  if (reading.show) {
    $('#reading-now').classList.remove('hidden');
    $('#reading-now').textContent = meta.text;
  }
  $$('#news-list li, #lib-list li').forEach((li) => li.classList.remove('now'));
}
function pauseReading() {
  if (!reading.active || reading.paused) return;
  reading.paused = true;
  ctx?.suspend();
  $('#pl-toggle').textContent = '▶';
  $('#pl-pos').textContent += ' · pausado';
}
function resumeReading() {
  if (reading.active && reading.paused) {
    reading.paused = false;
    ctx?.resume();
    $('#pl-toggle').textContent = '⏸';
    $('#pl-pos').textContent = $('#pl-pos').textContent.replace(' · pausado', '');
    return;
  }
  if (!reading.active && reading.title) {
    // retoma uma leitura parada a partir do último trecho
    ctx?.resume();
    send({ type: 'reader_seek', index: reading.index });
  }
}
function stopReading(keepPosition = false) {
  if (!reading.active) return;
  reading.active = false;
  reading.paused = false;
  send({ type: 'reader_stop' });
  reading.gen = -2;
  playQueue.splice(0, playQueue.length, ...playQueue.filter((b) => !b.meta));
  if (playing?.buffer?.meta) { playing.onended = null; try { playing.stop(); } catch {} playing = null; }
  ctx?.resume();
  $('#pl-toggle').textContent = '▶';
  if (keepPosition) $('#pl-pos').textContent = `parado em ${reading.index + 1}/${reading.total} · ▶ continua`;
  else $('#player').classList.add('hidden');
  $('#reading-now').classList.add('hidden');
}
function finishReading() {
  reading.active = false;
  $('#pl-pos').textContent = 'leitura concluída';
  $('#pl-fill').style.width = '100%';
  $('#pl-toggle').textContent = '▶';
  setTimeout(() => { if (!reading.active) $('#player').classList.add('hidden'); }, 6000);
}
function seek(delta) {
  if (!reading.title) return;
  const index = Math.max(0, reading.index + delta);
  reading.active = false;
  playQueue.splice(0, playQueue.length, ...playQueue.filter((b) => !b.meta));
  if (playing?.buffer?.meta) { playing.onended = null; try { playing.stop(); } catch {} playing = null; }
  ctx?.resume();
  reading.paused = false;
  send({ type: 'reader_seek', index });
}
$$('[data-pl]').forEach((b) => (b.onclick = () => {
  const a = b.dataset.pl;
  if (a === 'toggle') (reading.active && !reading.paused ? pauseReading : resumeReading)();
  else if (a === 'stop') stopReading(false);
  else if (a === 'prev') seek(-1);
  else if (a === 'next') seek(1);
  else if (a === 'open') openReader();
}));

// ------------------------------------------------------------ leitor: painel
let newsData = [];
let newsCat = 'Todas';
function openReader(tab) {
  $('#reader').classList.remove('hidden');
  if (tab) selectTab(tab);
  if (!newsData.length) loadNews();
  loadLibrary();
  loadReaderSettings();
}
function selectTab(tab) {
  $$('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  $$('#reader [data-pane]').forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== tab));
}
$$('.tabs button').forEach((b) => (b.onclick = () => selectTab(b.dataset.tab)));
$('#open-reader').onclick = () => openReader();
$('#quick-news').onclick = () => openReader('news');
$('#reader-close').onclick = () => $('#reader').classList.add('hidden');

async function loadNews(refresh = false) {
  $('#news-origin').textContent = 'carregando…';
  try {
    const d = await (await fetch(`/api/news${refresh ? '?refresh=true' : ''}`)).json();
    newsData = d.items || [];
    $('#news-origin').textContent = d.items?.length ? `${d.items.length} manchetes · ${d.origem}` : 'NewsFlash indisponível ou desligado';
  } catch { $('#news-origin').textContent = 'erro ao carregar'; }
  renderNews();
}
const newsInCat = () => newsData.filter((n) => newsCat === 'Todas' || (n.categoria || 'Outras') === newsCat);
function renderNews() {
  const cats = ['Todas', ...new Set(newsData.map((n) => n.categoria || 'Outras'))];
  const box = $('#news-cats');
  box.innerHTML = '';
  for (const c of cats) {
    const b = document.createElement('button');
    const count = c === 'Todas' ? newsData.length : newsData.filter((n) => (n.categoria || 'Outras') === c).length;
    b.innerHTML = `<span></span><i>${count}</i>`;
    b.querySelector('span').textContent = c;
    b.classList.toggle('on', c === newsCat);
    b.onclick = () => { newsCat = c; renderNews(); };
    box.append(b);
  }
  const ul = $('#news-list');
  ul.innerHTML = '';
  for (const n of newsInCat()) {
    const li = document.createElement('li');
    li.innerHTML = `<div class="ttl"></div><div class="meta"><b></b><span></span></div><div class="sum"></div>
      <div class="acts"><button data-a="sum">▶ Ler resumo</button><button data-a="full">▶ Matéria completa</button><button data-a="open">↗ Abrir</button></div>`;
    li.querySelector('.ttl').textContent = n.titulo;
    li.querySelector('.meta b').textContent = n.categoria || '';
    li.querySelector('.meta span').textContent = `${n.fonte} · ${n.quando}`;
    li.querySelector('.sum').textContent = n.resumo || '';
    li.querySelector('[data-a=sum]').onclick = () => readNews([n], false, n.titulo);
    li.querySelector('[data-a=full]').onclick = () => readNews([n], true, n.titulo);
    li.querySelector('[data-a=open]').onclick = () => window.open(n.link, '_blank');
    ul.append(li);
  }
}
async function readNews(items, full, title) {
  stopSpeech();
  await ensureAudio();
  ctx.resume();
  send({ type: 'reader_play', source: 'news', items, full, title });
}
$('#news-read-all').onclick = () => readNews(newsInCat(), false, `Notícias · ${newsCat}`);
$('#news-refresh').onclick = () => loadNews(true);
$('#news-ask').onclick = () => {
  $('#reader').classList.add('hidden');
  stopSpeech();
  const cat = newsCat === 'Todas' ? '' : ` da categoria ${newsCat}`;
  send({ type: 'text', text: `Me dê as cinco notícias mais importantes${cat} dos meus feeds do NewsFlash, uma frase cada.` });
};

// biblioteca
async function loadLibrary() {
  try { renderLibrary(await (await fetch('/api/library')).json()); } catch {}
}
function renderLibrary(items) {
  const ul = $('#lib-list');
  ul.innerHTML = '';
  if (!items.length) ul.innerHTML = '<li class="empty"><span class="note">Nenhum documento ainda. Envie um PDF, livro ou texto acima.</span></li>';
  for (const d of items) {
    const pct = d.total ? Math.round((d.pos / d.total) * 100) : 0;
    const li = document.createElement('li');
    li.innerHTML = `<div class="ttl"></div><div class="meta"><b></b><span></span></div><div class="prog"><i></i></div>
      <div class="acts"><button data-a="cont">▶ ${d.pos ? 'Continuar' : 'Ler'}</button><button data-a="start">⏮ Do início</button>
      <button data-a="page">↷ Ir para página</button><button data-a="ask">✦ Perguntar à IA</button><button data-a="del">✕ Remover</button></div>`;
    li.querySelector('.ttl').textContent = d.name;
    li.querySelector('.meta b').textContent = d.name.split('.').pop().toUpperCase();
    li.querySelector('.meta span').textContent = `${d.pages} pág. · ${d.total} trechos · ${pct}% lido${d.warning ? ` · ⚠ ${d.warning}` : ''}`;
    li.querySelector('.prog i').style.width = `${pct}%`;
    const play = async (extra) => { stopSpeech(); await ensureAudio(); ctx.resume(); send({ type: 'reader_play', source: 'doc', doc: d.id, ...extra }); };
    li.querySelector('[data-a=cont]').onclick = () => play({});
    li.querySelector('[data-a=start]').onclick = () => play({ index: 0 });
    li.querySelector('[data-a=page]').onclick = () => {
      const pg = prompt(`Ir para qual página? (1 a ${d.pages})`);
      if (pg && Number(pg) > 0) play({ page: Number(pg) });
    };
    li.querySelector('[data-a=ask]').onclick = () => {
      const q = prompt('O que você quer saber sobre este documento? (deixe em branco para um resumo)') ?? null;
      if (q === null) return;
      $('#reader').classList.add('hidden');
      fetch(`/api/library/${d.id}/ask`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q }) });
    };
    li.querySelector('[data-a=del]').onclick = () => fetch(`/api/library/${d.id}`, { method: 'DELETE' });
    ul.append(li);
  }
}
async function uploadFiles(files) {
  for (const f of files) {
    $('#upload-state').textContent = `Enviando e extraindo o texto de "${f.name}"…`;
    try {
      const r = await fetch('/api/library/upload', { method: 'POST', body: f, headers: { 'X-Filename': encodeURIComponent(f.name) } });
      const d = await r.json();
      $('#upload-state').textContent = r.ok ? `✔ "${d.name}" pronto: ${d.pages} páginas, ${d.total} trechos.${d.warning ? ` ⚠ ${d.warning}` : ''}` : `✖ ${d.error}`;
    } catch (e) { $('#upload-state').textContent = `✖ Falha ao enviar: ${e.message}`; }
  }
  loadLibrary();
}
$('#upload').onchange = (e) => uploadFiles([...e.target.files]);
const drop = $('#drop');
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); uploadFiles([...e.dataTransfer.files]); });

// ajustes da leitura
async function loadReaderSettings() {
  const r = await (await fetch('/api/reader/settings')).json();
  $('#r-speed').value = r.speed; $('#r-speed-v').textContent = `${r.speed}×`;
  $('#r-voice').value = r.voice;
  $('#r-pitch').value = r.pitch || 1; $('#r-pitch-v').textContent = r.pitch ? r.pitch.toFixed(2) : 'voz';
  $('#r-pause').value = r.pause_ms; $('#r-pause-v').textContent = `${r.pause_ms} ms`;
  $('#r-show').checked = reading.show;
}
const saveReader = (d) => fetch('/api/reader/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) }).then(loadReaderSettings);
$('#r-speed').oninput = (e) => ($('#r-speed-v').textContent = `${e.target.value}×`);
$('#r-speed').onchange = (e) => saveReader({ speed: Number(e.target.value) });
$('#r-voice').onchange = (e) => saveReader({ voice: e.target.value });
$('#r-pitch').oninput = (e) => ($('#r-pitch-v').textContent = Number(e.target.value).toFixed(2));
$('#r-pitch').onchange = (e) => saveReader({ pitch: Number(e.target.value) });
$('#r-pause').oninput = (e) => ($('#r-pause-v').textContent = `${e.target.value} ms`);
$('#r-pause').onchange = (e) => saveReader({ pause_ms: Number(e.target.value) });
$('#r-show').onchange = (e) => { reading.show = e.target.checked; try { localStorage.setItem('mestre-show-text', e.target.checked ? '1' : '0'); } catch {} if (!reading.show) $('#reading-now').classList.add('hidden'); };
try { reading.show = localStorage.getItem('mestre-show-text') !== '0'; } catch {}
$('#r-test').onclick = () => readNews([{ fonte: 'Teste do leitor', titulo: 'Assim fica a leitura com os ajustes atuais.', resumo: 'Ajuste a velocidade, a voz, o tom e a pausa entre parágrafos até ficar confortável.' }], false, 'Teste da leitura');

// ------------------------------------------------------------ telemetria e relógio
function fmtRate(b) {
  if (b > 1e6) return `${(b / 1e6).toFixed(1)} MB/s`;
  if (b > 1e3) return `${(b / 1e3).toFixed(0)} kB/s`;
  return `${b.toFixed(0)} B/s`;
}
function meter(k, v) {
  const el = document.querySelector(`.meter[data-k="${k}"]`);
  if (!el) return;
  el.querySelector('b').textContent = v == null ? '—' : `${Math.round(v)}%`;
  el.style.setProperty('--v', `${v ?? 0}%`);
  el.classList.toggle('hot', v > 85);
}
async function poll() {
  try {
    const s = await (await fetch('/api/stats')).json();
    meter('cpu', s.cpu);
    meter('ram', s.ram);
    meter('disk', s.disk);
    meter('gpu', s.gpu?.util);
    meter('vram', s.gpu?.mem);
    $('#net').textContent = `↓ ${fmtRate(s.net.rx)}  ↑ ${fmtRate(s.net.tx)}`;
    $('#containers').textContent = `${s.containers} ativos`;
    $('#gputemp').textContent = s.gpu ? `${s.gpu.temp} °C` : '—';
    const h = Math.floor(s.uptime / 3600);
    $('#uptime').textContent = `${Math.floor(h / 24)}d ${h % 24}h`;
    $('#brain').textContent = `${s.brain === 'online' ? 'on-line' : 'off-line'} · ${s.model}`;
    $('#jobs').textContent = s.jobs ? `${s.jobs} rodando` : 'nenhuma';
    $('#agenda-n').textContent = `${s.agenda} itens`;
  } catch {}
}
setInterval(poll, 2000);
poll();
setInterval(() => {
  const d = new Date();
  $('#time').textContent = d.toLocaleTimeString('pt-BR');
  $('#date').textContent = d.toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' });
}, 500);

const ticks = $('#ticks');
for (let i = 0; i < 72; i++) {
  const a = (i / 72) * Math.PI * 2;
  const r2 = i % 6 === 0 ? 244 : 238;
  const l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  l.setAttribute('x1', Math.cos(a) * 232); l.setAttribute('y1', Math.sin(a) * 232);
  l.setAttribute('x2', Math.cos(a) * r2); l.setAttribute('y2', Math.sin(a) * r2);
  l.setAttribute('class', 'tick');
  ticks.append(l);
}

// ------------------------------------------------------------ inicialização
const BOOT = [
  'Carregando núcleo cognitivo', 'Reconhecimento de voz', 'Sintetizador de voz', 'Núcleo visual NCFN',
  'Guardião de segurança', 'Agenda e tarefas autônomas', 'Vigilância do sistema', 'Link com o cérebro',
];
async function boot() {
  const box = $('#boot-lines');
  for (const line of BOOT) {
    const d = document.createElement('div');
    d.innerHTML = `▸ ${line} … <span class="ok">OK</span>`;
    box.append(d);
    await new Promise((r) => setTimeout(r, 160));
  }
}
async function start() {
  $('#boot').classList.add('gone');
  await ensureAudio();
  await ctx.resume();
  try {
    await startMic();
  } catch (e) {
    addLog('warn', `Microfone indisponível: ${e.message}`);
  }
  send({ type: 'greet' });
}
$('#start').onclick = start;
boot().then(() => { if (new URLSearchParams(location.search).has('auto')) start(); });
connect();
