"""MESTRE — servidor local do assistente de voz.

Ouvido: Whisper large-v3-turbo na GPU (padrão) ou a API da OpenAI.
Cérebro: Claude Code em modo contínuo (stream-json), com o guardião
(hooks/guard.py) pedindo confirmação para o que é perigoso.
Voz: Kokoro (masculina grave ou feminina), Piper como reserva, falada frase
a frase enquanto a resposta chega.
Autonomia: agenda (lembretes e tarefas), tarefas em segundo plano e
vigilância do sistema, que avisa sozinha.

A interface (ui/) conversa por WebSocket em /ws.
"""
import asyncio
import base64
import io
import json
import os
import re
import subprocess
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import central
import news
import numpy as np
import permissions
import portal
import reader
import psutil
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
STATE = ROOT / 'state'
STATE.mkdir(exist_ok=True)
WORK = (HOME / 'Documentos' if (HOME / 'Documentos').is_dir() else HOME / 'Documents') / 'MESTRE'
(WORK / 'relatorios').mkdir(parents=True, exist_ok=True)
CONFIG_FILE = ROOT / 'config.json'
if not CONFIG_FILE.exists():  # primeira execução: parte do exemplo
    CONFIG_FILE.write_text((ROOT / 'config.example.json').read_text())
CONFIG = json.loads(CONFIG_FILE.read_text())
PENDING = STATE / 'pending.json'
APPROVED = STATE / 'approved.json'
ALWAYS = STATE / 'always.json'
SECRETS = STATE / 'secrets.json'
AGENDA = STATE / 'agenda.json'
SESSION_FILE = STATE / 'session.json'

STT_POOL = ThreadPoolExecutor(max_workers=1)
TTS_POOL = ThreadPoolExecutor(max_workers=1)


def save_config():
    CONFIG_FILE.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2))


def secrets():
    try:
        return json.loads(SECRETS.read_text())
    except (OSError, ValueError):
        return {}


# ================================================================ ouvido
_whisper = None


def whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(CONFIG['whisper_model'], device='cuda', compute_type='float16',
                                download_root=str(ROOT / 'models' / 'whisper'))
    return _whisper


HALLUCINATIONS = re.compile(
    r'^(obrigad[oa]( por assistir)?|legendas? (pela|por) .*|tchau|\.+|amara\.org.*|inscreva-se.*|'
    r'e aí|hum+|ah+|é+|uhum|música|aplausos|risos|sous-titrage.*|transcrição.*|assistente de voz.*|'
    r'a mão|ok|tá|sim|não)[.!?]*$', re.I)


def pcm_to_wav(pcm16: bytes, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def transcribe_openai(pcm16: bytes) -> str:
    import urllib.request
    key = secrets().get('openai_api_key') or os.environ.get('OPENAI_API_KEY')
    if not key:
        raise RuntimeError('Chave da OpenAI não configurada')
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for name, value in (('model', CONFIG.get('openai_stt_model', 'gpt-4o-transcribe')), ('language', 'pt')):
        body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    body.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fala.wav"\r\n'
               f'Content-Type: audio/wav\r\n\r\n'.encode())
    body.write(pcm_to_wav(pcm16))
    body.write(f'\r\n--{boundary}--\r\n'.encode())
    req = urllib.request.Request('https://api.openai.com/v1/audio/transcriptions', data=body.getvalue(),
                                 headers={'Authorization': f'Bearer {key}',
                                          'Content-Type': f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get('text', '').strip()


def transcribe(pcm16: bytes) -> str:
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    if len(audio) < 16000 * 0.35 or float(np.sqrt(np.mean(audio ** 2))) < 0.004:
        return ''
    if CONFIG.get('stt') == 'openai':
        try:
            text = transcribe_openai(pcm16)
        except Exception as e:
            print('[stt] OpenAI falhou, usando Whisper local:', e, flush=True)
            text = None
        if text is not None:
            return '' if HALLUCINATIONS.match(text) else text
    segs, _ = whisper().transcribe(audio, language='pt', beam_size=5, vad_filter=True,
                                   condition_on_previous_text=False, no_speech_threshold=0.5,
                                   vad_parameters={'min_silence_duration_ms': 400, 'threshold': 0.5})
    # Descarta trechos que o próprio Whisper acha que são silêncio ou chute.
    kept = [s.text.strip() for s in segs if s.no_speech_prob < 0.55 and s.avg_logprob > -0.9]
    text = ' '.join(kept).strip()
    if not text or HALLUCINATIONS.match(text):
        return ''
    print(f'[stt] {text}', flush=True)
    return text


# Tratamento pela voz: tom médio grave (< 155 Hz) é voz masculina e o MESTRE
# chama o usuário de "MESTRE"; agudo (> 175 Hz), feminina, e chama de "DÍVA"
# (as palavras mudam nas configurações). Na faixa do meio (ou com pouca voz)
# mantém o último tratamento.
ADDRESS_DEFAULT = {'m': 'MESTRE', 'f': 'DÍVA'}
ADDRESS_FILE = STATE / 'tratamento.json'


def address_word(voice: str | None = None) -> str:
    voice = voice or address
    return (CONFIG.get(f'address_{voice}') or '').strip() or ADDRESS_DEFAULT[voice]


def voice_pitch(pcm16: bytes) -> float | None:
    """Frequência fundamental mediana (Hz) dos trechos com voz, por autocorrelação."""
    x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    size, hop, lo, hi = 640, 160, 16000 // 400, 16000 // 60  # janelas de 40 ms; 60 a 400 Hz
    if len(x) < size * 4:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(x, size)[::hop]
    energy = np.sqrt((frames ** 2).mean(axis=1))
    f0 = []
    for frame in frames[energy > max(0.01, np.percentile(energy, 60) * 0.5)]:
        frame = (frame - frame.mean()) * np.hanning(size)
        spec = np.fft.rfft(frame, 2 * size)
        ac = np.fft.irfft(spec * np.conj(spec))[:hi + 1]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        seg = ac[lo:hi + 1]
        peak = seg.max()
        if peak < 0.45:
            continue
        # o menor atraso perto do máximo evita pegar a oitava de baixo; depois
        # sobe até o topo desse pico e refina entre as amostras (parábola)
        i = int(np.argmax(seg >= peak * 0.85))
        while i + 1 < len(seg) and seg[i + 1] > seg[i]:
            i += 1
        a, b, c = seg[max(i - 1, 0)], seg[i], seg[min(i + 1, len(seg) - 1)]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c < 0 else 0.0
        f0.append(16000 / (lo + i + shift))
    return float(np.median(f0)) if len(f0) >= 15 else None


SENHOR = re.compile(r'\b(?:[Oo]s |[Aa]s |[Oo] |[Aa] )?(senhor(?:a|es|as)?|Senhor(?:a|es|as)?)\b')


def fix_address(text: str) -> str:
    """Rede de segurança: o hábito antigo de dizer "senhor" vira o tratamento atual."""
    return SENHOR.sub(lambda m: address_word(), text)


def load_address() -> str:
    try:
        voice = json.loads(ADDRESS_FILE.read_text())['voz']
    except (OSError, ValueError, KeyError):
        voice = 'm'
    return voice if voice in ADDRESS_DEFAULT else 'm'


def update_address(pcm16: bytes):
    global address, last_pitch
    f0 = voice_pitch(pcm16)
    last_pitch = f0 or last_pitch
    if f0 is not None and (f0 < 155 or f0 > 175):
        new = 'm' if f0 < 155 else 'f'
        if new != address:
            address = new
            ADDRESS_FILE.write_text(json.dumps({'voz': address}))
        print(f'[voz] {f0:.0f} Hz → {address_word()}', flush=True)


address = load_address()
last_pitch = None


# Interrupção por voz: a janela pausa a fala ao detectar voz e manda o trecho
# para cá. Só conta como interrupção se o Whisper ouvir palavras de verdade que
# não sejam o eco do que o próprio MESTRE estava dizendo.
STOP_WORDS = re.compile(r'(mestre\W*)?(para|pare|parar|chega|basta|sil[eê]ncio|quieto|espera|espere|stop|'
                        r'cala a boca|pode parar|para com isso|ok\W*(para|chega))(\W+(a[ií]|um pouco|por favor|agora))?\W*',
                        re.I)


def _words(text: str) -> list[str]:
    return re.findall(r'\w+', text.lower())


def is_echo(text: str, recent: list) -> bool:
    heard = _words(text)
    said = {w for r in recent for w in _words(str(r))}
    return bool(heard and said) and sum(w in said for w in heard) / len(heard) >= 0.6


# ================================================================ voz
_kokoro = None
_piper = None


def kokoro():
    global _kokoro
    if _kokoro is None:
        import torch
        from kokoro import KPipeline
        _kokoro = KPipeline(lang_code='p', repo_id='hexgrad/Kokoro-82M',
                            device='cuda' if torch.cuda.is_available() else 'cpu')
    return _kokoro


def piper():
    global _piper
    if _piper is None:
        from piper import PiperVoice
        _piper = PiperVoice.load(str(ROOT / 'models' / 'piper' / 'pt_BR-faber-medium.onnx'))
    return _piper


def voice_profile():
    return CONFIG['voices'][CONFIG.get('gender', 'masculino')]


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes()
    return pcm_to_wav(pcm, rate)


def synthesize(text: str, voice: str | None = None, pitch: float | None = None, speed: float | None = None,
               tail_ms: int = 0) -> bytes:
    """Fala com o perfil da voz atual. O tom grave vem de tocar o áudio numa
    taxa menor (pitch < 1), com a fala gerada mais rápida na mesma proporção
    para a duração não mudar."""
    prof = dict(voice_profile())
    if voice:
        prof['voice'] = voice
        prof['engine'] = 'piper' if voice.startswith('piper') else 'kokoro'
    pitch = pitch or prof.get('pitch', 1.0)
    speed = speed or prof.get('speed', 1.0)
    silence = np.zeros(int(24000 * tail_ms / 1000), dtype=np.float32)
    if prof.get('voice', '').startswith('piper'):
        prof = {**prof, 'engine': 'piper'}
    if prof.get('engine', 'kokoro') == 'kokoro':
        try:
            parts = [a.numpy() if hasattr(a, 'numpy') else a
                     for _, _, a in kokoro()(text, voice=prof['voice'], speed=speed / pitch)]
            audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
            return wav_bytes(np.concatenate([audio, silence]), int(24000 * pitch))
        except Exception as e:
            print('[tts] Kokoro falhou, usando Piper:', e, flush=True)
    from piper import SynthesisConfig
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        piper().synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=0.95 * pitch))
    with wave.open(io.BytesIO(buf.getvalue())) as r:
        rate, frames = r.getframerate(), r.readframes(r.getnframes())
    frames += b'\x00\x00' * int(rate * tail_ms / 1000)
    return pcm_to_wav(frames, int(rate * pitch))


def speakable(text: str) -> str:
    t = re.sub(r'```.*?```', ' ', text, flags=re.S)
    t = re.sub(r'`([^`]*)`', r'\1', t)
    t = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', t)
    t = re.sub(r'https?://\S+', 'o link', t)
    t = re.sub(r'[*_#>|~]+', ' ', t)
    t = re.sub(r'^\s*[-•]\s+', '', t, flags=re.M)
    t = t.replace('%', ' por cento').replace('&', ' e ').replace('/', ' barra ')
    for voice in ADDRESS_DEFAULT:  # em maiúsculas a voz soletraria a palavra
        word = address_word(voice)
        t = re.sub(rf'\b{re.escape(word)}\b', word.capitalize(), t)
    return re.sub(r'\s+', ' ', t).strip()


# ================================================================ cérebro
EMOTIONS = ('neutro', 'feliz', 'sorriso', 'empolgado', 'confiante', 'ironico', 'pensativo', 'curioso', 'surpreso',
            'preocupado', 'triste', 'determinado', 'serio', 'alerta')
EMO_TAG = re.compile(r'\s*\[(\w+)\]\s*')


def strip_tags(text: str) -> str:
    return EMO_TAG.sub(lambda m: ' ' if m.group(1).lower() in EMOTIONS else m.group(0), text)


def persona_prompt():
    base = (ROOT / 'persona.md').read_text()
    local = ROOT / 'persona.local.md'  # contexto pessoal, fora do git
    if local.exists():
        base += '\n\n' + local.read_text()
    base += memory_prompt()
    if CONFIG.get('gender') == 'feminino':
        base += ('\n\n## Gênero\nNesta configuração sua voz e seu rosto são femininos: fale de si no '
                 'feminino ("estou pronta", "fiquei curiosa"). Seu nome continua MESTRE.\n')
    # Por último, para valer sobre a memória e sobre o hábito das conversas antigas.
    base += (f'\n\n## Tratamento (prevalece sobre memória e conversas anteriores)\n'
             f'Chame o usuário pela palavra da etiqueta `[tratamento: X]` da mensagem: hoje "{address_word("m")}" '
             f'para voz masculina e "{address_word("f")}" para voz feminina. Nunca diga "senhor" nem "senhora", '
             'mesmo que tenha dito antes nesta conversa.\n')
    return base


def memory_prompt() -> str:
    """Memória de longo prazo (pasta do Obsidian): onde fica e o que já se sabe."""
    mem = permissions.memory_dir()
    if not mem or not permissions.current().get('vault', True):
        return ''
    d = Path(mem)
    parts = [f'\n\n## Sua memória de longo prazo\n\nPasta: `{mem}` (cofre Obsidian do usuário). '
             'É a ÚNICA memória que você deve usar para guardar coisas: não grave no sistema de memória interno '
             'do Claude Code (~/.claude/projects/.../memory), mesmo que outras instruções sugiram. '
             'As regras de uso estão na seção "Memória" acima. Conteúdo atual das notas principais:\n']
    for name in ('00 - ÍNDICE.md', 'SOBRE O USUÁRIO.md'):
        f = d / name
        if f.exists():
            parts.append(f'\n### {name}\n\n{f.read_text()[:4000]}\n')
    conv = d / 'CONVERSAS.md'
    if conv.exists() and conv.stat().st_size:
        parts.append(f'\n### CONVERSAS.md (final)\n\n…{conv.read_text()[-2500:]}\n')
    return ''.join(parts)


MEMORY_SEED = {
    '00 - ÍNDICE.md': '''---
tags: [assistente, memoria, indice]
criado: {hoje}
atualizado: {hoje}
---
# Memória do assistente MESTRE

Memória de longo prazo do **MESTRE**: ele guarda aqui, sem perguntar, o que for importante lembrar
entre as conversas. Você pode ler, corrigir ou apagar qualquer nota.

## Notas

- [[SOBRE O USUÁRIO]]: quem é o usuário, preferências e jeito de trabalhar
- [[CONVERSAS]]: diário das conversas relevantes

## Convenções

- **Notas:** uma por assunto, com o tipo no título: `PROJETO - …`, `PESSOA - …`, `DECISÃO - …`, `PESQUISA - …`.
- **Frontmatter:** `tags`, `criado` e `atualizado`, com datas no formato AAAA-MM-DD.
- **Ligações:** links `[[…]]` entre notas relacionadas.
- **Segredos:** nunca senhas, tokens ou chaves.
''',
    'SOBRE O USUÁRIO.md': '''---
tags: [assistente, memoria, usuario]
criado: {hoje}
atualizado: {hoje}
---
# Sobre o usuário

## Perfil

## Preferências
''',
    'CONVERSAS.md': '''---
tags: [assistente, memoria, diario]
criado: {hoje}
atualizado: {hoje}
---
# Conversas com o MESTRE

Registro das conversas relevantes, da mais antiga para a mais recente.
''',
}


def seed_memory(path: Path):
    """Cria a pasta de memória e as notas iniciais que estiverem faltando (nunca sobrescreve)."""
    path.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now().strftime('%Y-%m-%d')
    for name, body in MEMORY_SEED.items():
        f = path / name
        if not f.exists() or f.stat().st_size == 0:
            f.write_text(body.replace('{hoje}', hoje))


def memory_info():
    raw = CONFIG.get('memory_dir') or ''
    d = Path(raw).expanduser() if raw else None
    ok = bool(d and d.is_dir())
    return {'memory_dir': raw, 'memory_ok': ok,
            'memory_notes': len(list(d.rglob('*.md'))) if ok else 0}


def claude_settings_file():
    settings = {'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [
        {'type': 'command', 'command': f'{ROOT}/.venv/bin/python {ROOT}/hooks/guard.py'}]}]}}
    path = STATE / 'settings.json'
    path.write_text(json.dumps(settings))
    return path


def claude_env():
    return {**os.environ, 'MESTRE_STATE': str(STATE), 'MESTRE_URL': f"http://127.0.0.1:{CONFIG['port']}",
            'PATH': f"{ROOT / 'bin'}:{HOME / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
            'DISPLAY': os.environ.get('DISPLAY') or detect_display(),
            'XAUTHORITY': os.environ.get('XAUTHORITY') or f'/run/user/{os.getuid()}/gdm/Xauthority'}


def detect_display():
    try:
        socks = sorted(f for f in os.listdir('/tmp/.X11-unix') if f.startswith('X'))
        return ':' + socks[0][1:] if socks else ':0'
    except OSError:
        return ':0'


def claude_flags():
    blocked = ['AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode', *permissions.disallowed_tools(permissions.current())]
    return ['--permission-mode', 'bypassPermissions', '--disallowedTools', ','.join(blocked)]


class Brain:
    """Um processo `claude -p` contínuo (stream-json) que guarda a conversa."""

    def __init__(self, emit, profile='desktop'):
        self.emit = emit
        self.profile = profile
        self.session_file = SESSION_FILE if profile == 'desktop' else STATE / f'session-{profile}.json'
        self.proc = None
        self.busy = False
        self.session = None
        self.at_message_start = True
        self.head = ''
        self.restart_when_idle = False
        # Sem a permissão "Memória da conversa", cada abertura começa do zero.
        if permissions.current().get('memory', True):
            try:
                self.session = json.loads(self.session_file.read_text()).get('session_id')
            except (OSError, ValueError):
                pass

    async def start(self):
        portal_side = self.profile == 'portal'
        cmd = ['claude', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
               '--verbose', '--include-partial-messages', *(portal_flags() if portal_side else claude_flags()),
               '--settings', str(claude_settings_file()),
               '--append-system-prompt', portal_prompt() if portal_side else persona_prompt(),
               '--model', CONFIG['model']]
        if self.session:
            cmd += ['--resume', self.session]
        cwd, env = HOME, claude_env()
        if portal_side:
            cwd = STATE / 'portal'
            cwd.mkdir(exist_ok=True)
            env['MESTRE_PROFILE'] = 'portal'
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024)
        asyncio.create_task(self._read(self.proc))
        asyncio.create_task(self._stderr(self.proc))

    async def _stderr(self, proc):
        async for line in proc.stderr:
            msg = line.decode(errors='replace').strip()
            if msg:
                print('[claude]', msg[:300], flush=True)

    async def ask(self, text: str):
        if not self.proc or self.proc.returncode is not None:
            await self.start()
        self.busy = True
        line = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}, ensure_ascii=False)
        self.proc.stdin.write((line + '\n').encode())
        await self.proc.stdin.drain()

    async def interrupt(self):
        if self.proc and self.busy and self.proc.returncode is None:
            req = {'type': 'control_request', 'request_id': f'int-{time.time()}', 'request': {'subtype': 'interrupt'}}
            self.proc.stdin.write((json.dumps(req) + '\n').encode())
            await self.proc.stdin.drain()

    async def restart(self, keep_session=True):
        if not keep_session:
            self.session = None
            self.session_file.unlink(missing_ok=True)
        old = self.proc
        self.proc = None
        if old and old.returncode is None:
            old.kill()
        self.busy = False
        await self.start()

    async def _text(self, text):
        await self.emit('delta', text)

    async def _read(self, proc):
        async for raw in proc.stdout:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            t = d.get('type')
            if d.get('session_id') and d.get('session_id') != self.session:
                self.session = d['session_id']
                self.session_file.write_text(json.dumps({'session_id': self.session}))
            if t == 'stream_event':
                ev = d.get('event', {})
                et = ev.get('type')
                if et == 'message_start':
                    self.at_message_start = True
                    self.head = ''
                elif et == 'content_block_delta' and ev.get('delta', {}).get('type') == 'text_delta':
                    await self._text(ev['delta']['text'])
                elif et == 'message_stop':
                    await self.emit('flush', None)
            elif t == 'assistant':
                for block in d.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_use':
                        await self.emit('tool', {'name': block.get('name'), 'input': block.get('input')})
            elif t == 'user':
                content = d.get('message', {}).get('content')
                for block in content if isinstance(content, list) else []:
                    if block.get('type') == 'tool_result' and block.get('is_error'):
                        await self.emit('tool_error', str(block.get('content'))[:300])
            elif t == 'result':
                self.busy = False
                await self.emit('done', {'cost': d.get('total_cost_usd'), 'error': d.get('is_error')})
        if proc is self.proc:
            self.busy = False
            await self.emit('done', {'error': True, 'exited': True})


# ================================================================ interface
app = FastAPI()
app.mount('/ui', StaticFiles(directory=ROOT / 'ui'), name='ui')


@app.middleware('http')
async def no_stale_ui(request, call_next):
    # A interface muda com frequência: o navegador sempre confere a versão.
    response = await call_next(request)
    if request.url.path == '/' or request.url.path.startswith('/ui/'):
        response.headers['Cache-Control'] = 'no-cache'
    return response
clients: set[WebSocket] = set()


async def broadcast(msg: dict):
    for ws in list(clients):
        try:
            await ws.send_json(msg)
        except Exception:
            clients.discard(ws)


class Mouth:
    """Junta o texto em frases e fala uma de cada vez, em ordem."""

    SENTENCE = re.compile(r'(.+?[.!?…:;](?=\s|$)|.+?\n)', re.S)

    def __init__(self, send=None):
        self.send = send or broadcast
        self.buffer = ''
        self.queue: asyncio.Queue = asyncio.Queue()
        self.generation = 0
        self.emotion = 'neutro'
        asyncio.create_task(self._worker())

    def feed(self, text):
        self.buffer += text
        while True:
            m = self.SENTENCE.match(self.buffer)
            if not m:
                break
            end = m.end()
            if len(m.group(1).strip()) < 18 and not m.group(1).endswith('\n'):
                nxt = self.SENTENCE.match(self.buffer, end)
                if not nxt:
                    break
                end = nxt.end()
            self.queue.put_nowait((self.generation, self.buffer[:end]))
            self.buffer = self.buffer[end:]

    def flush(self):
        if self.buffer.strip():
            self.queue.put_nowait((self.generation, self.buffer))
        self.buffer = ''

    def say(self, text):
        """Fala avulsa (lembretes, alertas, saudação)."""
        self.queue.put_nowait((self.generation, text))

    def stop(self):
        self.generation += 1
        self.buffer = ''
        self.emotion = 'neutro'

    async def _worker(self):
        loop = asyncio.get_running_loop()
        while True:
            gen, text = await self.queue.get()
            if gen != self.generation:
                continue
            # A emoção vale a partir da frase em que a etiqueta aparece.
            tags = [t.lower() for t in EMO_TAG.findall(text) if t.lower() in EMOTIONS]
            if tags:
                self.emotion = tags[0]
            emotion = self.emotion
            if len(tags) > 1:
                self.emotion = tags[-1]
            spoken = speakable(fix_address(strip_tags(text)))
            if not spoken:
                continue
            try:
                audio = await loop.run_in_executor(TTS_POOL, synthesize, spoken)
            except Exception as e:
                print('[tts]', e, flush=True)
                continue
            if gen != self.generation:
                continue
            await self.send({'type': 'say', 'text': spoken, 'emotion': emotion,
                             'audio': base64.b64encode(audio).decode()})


mouth: Mouth | None = None
brain: Brain | None = None
follow_up_until = 0.0
mode = CONFIG.get('mode', 'free')
CONFIRM = re.compile(r'\b(confirm[oa]|confirmad[oa]|pode (fazer|seguir|ir|executar)|autorizo|sim,? pode)\b', re.I)
ALWAYS_WORDS = re.compile(r'\bsempre\b', re.I)
DENY = re.compile(r'\b(n[ãa]o|cancela|cancelar|negativo|nego|esquece)\b', re.I)
WAKE = re.compile(r'\b(mestre|meste|mestra|mestres|maestro)\b[\s,.!?:]*', re.I)


_ui_hold = ''


async def on_brain(kind, payload):
    global follow_up_until, _ui_hold
    if kind == 'delta':
        mouth.feed(payload)
        text = _ui_hold + payload
        cut = text.rfind('[')
        word = re.search(r'\w+$', text)  # palavra pela metade: espera o resto (troca de "senhor")
        if cut != -1 and ']' not in text[cut:] and len(text) - cut < 16:
            text, _ui_hold = text[:cut], text[cut:]
        elif word and len(text) - word.start() < 12:
            text, _ui_hold = text[:word.start()], text[word.start():]
        else:
            _ui_hold = ''
        text = fix_address(strip_tags(text))
        if text:
            await broadcast({'type': 'delta', 'text': text})
        first = EMO_TAG.match(payload)
        if first and first.group(1).lower() in EMOTIONS:
            await broadcast({'type': 'emotion', 'name': first.group(1).lower()})
    elif kind == 'emotion':
        await broadcast({'type': 'emotion', 'name': payload})
    elif kind == 'flush':
        mouth.flush()
        if _ui_hold:
            await broadcast({'type': 'delta', 'text': fix_address(strip_tags(_ui_hold))})
            _ui_hold = ''
    elif kind == 'tool':
        await broadcast({'type': 'tool', 'name': payload['name'], 'detail': describe_tool(payload)})
    elif kind == 'tool_error':
        if 'AÇÃO PROTEGIDA' in payload and PENDING.exists():
            p = json.loads(PENDING.read_text())
            await broadcast({'type': 'pending', 'why': p['why'], 'summary': p['summary'], 'tool': p['tool']})
    elif kind == 'done':
        mouth.flush()
        if brain.restart_when_idle and not payload.get('exited'):
            brain.restart_when_idle = False
            asyncio.create_task(brain.restart(keep_session=True))
        follow_up_until = time.time() + CONFIG.get('follow_up_seconds', 12)
        await broadcast({'type': 'done', **(payload or {})})


def describe_tool(t):
    name = t['name'] or ''
    i = t.get('input') or {}
    if name == 'Bash':
        return str(i.get('command') or i.get('description', ''))[:160]
    if name.startswith('mcp__playwright__'):
        return 'navegador: ' + name.split('__')[-1].replace('browser_', '') + ' ' + str(i.get('url') or i.get('element') or '')[:100]
    for k in ('file_path', 'url', 'query', 'pattern', 'description'):
        if i.get(k):
            return f'{name}: {str(i[k])[:140]}'
    return name


def always_rules():
    try:
        return json.loads(ALWAYS.read_text())
    except (OSError, ValueError):
        return []


async def decide_pending(choice: str):
    """Decisão sobre a ação protegida: 'once', 'always' ou 'deny'."""
    if not PENDING.exists():
        return False
    pending = json.loads(PENDING.read_text())
    PENDING.unlink(missing_ok=True)
    if choice == 'deny':
        await broadcast({'type': 'denied', 'summary': pending['summary']})
        await send_to_brain('Não. Cancele essa ação.', echo=False)
        return True
    if choice == 'always':
        rules = always_rules()
        rule = {'tool': pending['tool'], 'match': pending.get('match') or pending['summary'],
                'why': pending['why'], 'added': datetime.now().isoformat(timespec='seconds')}
        if rule['match'] not in [r['match'] for r in rules]:
            rules.append(rule)
            ALWAYS.write_text(json.dumps(rules, ensure_ascii=False, indent=1))
    APPROVED.write_text(json.dumps({'fp': pending['fp'], 'expires': time.time() + 180}))
    await broadcast({'type': 'approved', 'summary': pending['summary'], 'always': choice == 'always'})
    await send_to_brain('Confirmo.', echo=False)
    return True


async def send_to_brain(text: str, echo=True):
    mouth.stop()
    if echo:
        await broadcast({'type': 'user', 'text': text})
    await broadcast({'type': 'status', 'state': 'thinking'})
    if brain.busy:
        await brain.interrupt()
        await asyncio.sleep(0.3)
    await brain.ask(f'[tratamento: {address_word()} (nunca "senhor")] {text}')


async def handle_utterance(text: str, source: str):
    """Fala ou texto do usuário: confirmação, palavra de ativação e envio ao cérebro."""
    text = text.strip()
    if not text:
        return
    if source == 'voice' and mode == 'wake' and time.time() > follow_up_until and not brain.busy:
        m = WAKE.search(text)
        if not m:
            await broadcast({'type': 'heard', 'text': text, 'ignored': True})
            await broadcast({'type': 'status', 'state': 'idle'})
            return
        if not text[:m.start()].strip(' ,.!?:;-'):
            text = text[m.end():].strip() or 'Você está aí?'
    if PENDING.exists():
        pending = json.loads(PENDING.read_text())
        if time.time() - pending.get('at', 0) < 300:
            choice = None
            if ALWAYS_WORDS.search(text) and (CONFIRM.search(text) or 'pode' in text.lower()):
                choice = 'always'
            elif CONFIRM.search(text):
                choice = 'once'
            elif DENY.search(text):
                choice = 'deny'
            if choice:
                await broadcast({'type': 'user', 'text': text})
                await decide_pending(choice)
                return
        else:
            PENDING.unlink(missing_ok=True)
    await send_to_brain(text)


# ================================================================ autonomia: agenda e tarefas
def load_agenda():
    try:
        return json.loads(AGENDA.read_text())
    except (OSError, ValueError):
        return []


def save_agenda(items):
    AGENDA.write_text(json.dumps(items, ensure_ascii=False, indent=1))


def repeat_step(repeat: str):
    if repeat in ('diario', 'dias-uteis'):
        return timedelta(days=1)
    if repeat == 'semanal':
        return timedelta(weeks=1)
    if repeat.startswith('horas:'):
        return timedelta(hours=float(repeat.split(':')[1]))
    if repeat.startswith('minutos:'):
        return timedelta(minutes=max(1.0, float(repeat.split(':')[1])))
    return None


def next_time(when: float, repeat: str | None):
    step = repeat_step(repeat) if repeat else None
    if not step:
        return None
    dt = datetime.fromtimestamp(when) + step
    while dt.timestamp() < time.time() or (repeat == 'dias-uteis' and dt.weekday() >= 5):
        dt += step
    return dt.timestamp()


jobs: dict[str, dict] = {}


async def run_task(text: str, origin='pedido'):
    """Tarefa autônoma num processo `claude -p` separado: a conversa continua livre."""
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {'id': job_id, 'text': text, 'started': time.time(), 'status': 'rodando', 'origin': origin}
    await broadcast({'type': 'task', 'job': jobs[job_id]})
    prompt = (f'TAREFA AUTÔNOMA ({origin}). O usuário não está acompanhando em tempo real e não pode '
              f'confirmar nada agora: ações protegidas serão negadas, então faça o que for possível sem elas '
              f'e diga o que ficou pendente. Ao terminar, responda só com um resumo falado de uma a três '
              f'frases, sem etiqueta de emoção e sem markdown.\n\nTarefa: {text}')
    cmd = ['claude', '-p', prompt, '--output-format', 'json', *claude_flags(),
           '--settings', str(claude_settings_file()), '--append-system-prompt', persona_prompt(),
           '--model', CONFIG['model']]
    env = {**claude_env(), 'MESTRE_AUTONOMOUS': '1'}
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(HOME), env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=CONFIG.get('task_timeout', 1800))
        result = json.loads(out or b'{}').get('result', '').strip() or 'A tarefa terminou sem resposta.'
        status = 'concluída'
    except asyncio.TimeoutError:
        result, status = 'A tarefa passou do tempo limite e foi interrompida.', 'tempo esgotado'
    except Exception as e:
        result, status = f'A tarefa falhou: {e}', 'falhou'
    result = strip_tags(result).strip()
    report = WORK / 'relatorios' / f"{datetime.now():%Y-%m-%d_%H%M}_{job_id}.md"
    report.write_text(f'# Tarefa {job_id}\n\n**Pedido:** {text}\n\n**Situação:** {status}\n\n{result}\n')
    jobs[job_id].update(status=status, result=result, finished=time.time(), report=str(report))
    await broadcast({'type': 'task', 'job': jobs[job_id]})
    await broadcast({'type': 'emotion', 'name': 'sorriso' if status == 'concluída' else 'preocupado'})
    mouth.say(f'Tarefa em segundo plano {status}. {result}')
    subprocess.Popen(['notify-send', '-i', str(HOME / '.local/share/icons/mestre.svg'), 'MESTRE', result[:250]])


async def agenda_loop():
    while True:
        await asyncio.sleep(15)
        items = load_agenda()
        now = time.time()
        changed = False
        for item in list(items):
            if item['when'] > now:
                continue
            changed = True
            if item['kind'] == 'tarefa':
                asyncio.create_task(run_task(item['text'], origin='agendada'))
            else:
                await broadcast({'type': 'reminder', 'item': item})
                await broadcast({'type': 'emotion', 'name': 'alerta'})
                mouth.say(f"Lembrete: {item['text']}")
                subprocess.Popen(['notify-send', '-u', 'critical', '-i', str(HOME / '.local/share/icons/mestre.svg'),
                                  'MESTRE — lembrete', item['text']])
            nxt = next_time(item['when'], item.get('repeat'))
            if nxt:
                item['when'] = nxt
            else:
                items.remove(item)
        if changed:
            save_agenda(items)
            await broadcast({'type': 'agenda', 'items': items})


async def split_loop():
    """Fechou a janela aberta ao lado? O MESTRE volta à tela cheia."""
    split = Path(f'/tmp/mestre-split-{os.getuid()}.json')
    while True:
        await asyncio.sleep(3)
        if split.exists():
            proc = await asyncio.create_subprocess_exec(str(ROOT / 'bin' / 'mestre-pc'), 'split-check', env=claude_env(),
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()


# ================================================================ autonomia: vigilância
def http_ok(url):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


async def watch_loop():
    """Avisa sozinho quando algo sai do normal (uma vez por problema)."""
    alerted: set[str] = set()
    baseline = None
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(60)
        if not CONFIG.get('watch', True):
            continue
        problems = {}
        if psutil.disk_usage(str(HOME)).percent > 90:
            problems['disco'] = 'o disco principal passou de noventa por cento'
        g = gpu_stats()
        if g and g['temp'] >= 85:
            problems['gpu'] = f"a placa de vídeo está a {int(g['temp'])} graus"
        temps = psutil.sensors_temperatures() if hasattr(psutil, 'sensors_temperatures') else {}
        cpu_t = max((t.current for v in temps.values() for t in v if t.current), default=0)
        if cpu_t >= 95:
            problems['cpu'] = f'o processador chegou a {int(cpu_t)} graus'
        if psutil.virtual_memory().percent > 92:
            problems['ram'] = 'a memória está quase cheia'
        bat = psutil.sensors_battery()
        if bat and not bat.power_plugged and bat.percent < 15:
            problems['bateria'] = f'a bateria está em {int(bat.percent)} por cento e o carregador desligado'
        try:
            # Só containers feitos para ficar no ar (com política de reinício); temporários
            # (testes, `docker run --rm`) e os apagados de propósito não geram alerta.
            ids = subprocess.run(['docker', 'ps', '-q'], capture_output=True, text=True, timeout=5).stdout.split()
            out = subprocess.run(['docker', 'inspect', '-f', '{{.Name}} {{.HostConfig.RestartPolicy.Name}}', *ids],
                                 capture_output=True, text=True, timeout=5).stdout if ids else ''
            names = {n.lstrip('/') for n, policy in (l.split() for l in out.splitlines() if l.strip())
                     if policy not in ('', 'no')}
            existing = set(subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}'], capture_output=True,
                                          text=True, timeout=5).stdout.split())
            if baseline is None:
                baseline = set(names)
            for gone in baseline - names:
                if gone in existing:
                    problems[f'container:{gone}'] = f'o container {gone} parou'
                else:
                    baseline.discard(gone)
            baseline |= names
        except Exception:
            pass
        for key, url in CONFIG.get('watch_urls', {}).items():
            if not await loop.run_in_executor(None, http_ok, url):
                problems[f'url:{key}'] = f'o {key} não está respondendo'
        for key, msg in problems.items():
            if key not in alerted:
                alerted.add(key)
                await broadcast({'type': 'alert', 'text': msg})
                await broadcast({'type': 'emotion', 'name': 'alerta'})
                mouth.say(f'Atenção: {msg}.')
                subprocess.Popen(['notify-send', '-u', 'critical', 'MESTRE — alerta', msg])
        for key in list(alerted):
            if key not in problems:
                alerted.discard(key)
                if key.startswith('container:') and baseline is not None:
                    baseline.discard(key.split(':', 1)[1])


# ================================================================ leitor (livros e notícias)
READER_DEFAULTS = {'speed': 1.0, 'voice': 'auto', 'pitch': 0, 'pause_ms': 350, 'ahead': 2}


def reader_settings():
    return {**READER_DEFAULTS, **CONFIG.get('reader', {})}


class Reader:
    """Uma leitura por vez. O servidor manda os trechos sob demanda ('reader_need'),
    sempre alguns à frente do que está tocando; a posição de cada livro fica salva."""

    def __init__(self):
        self.gen = 0
        self.source = None  # {'kind': 'doc'|'news', 'title', 'doc_id'?}
        self.items: list[dict] = []
        self.next = 0
        self.lock = asyncio.Lock()

    def state(self):
        return {'title': self.source['title'] if self.source else '', 'total': len(self.items),
                'kind': self.source['kind'] if self.source else None,
                'doc_id': (self.source or {}).get('doc_id')}

    async def start(self, source: dict, items: list[dict], index: int = 0):
        self.gen += 1
        self.source, self.items = source, items
        self.next = max(0, min(index, len(items)))
        mouth.stop()
        await broadcast({'type': 'reader_start', **self.state(), 'index': self.next})
        for _ in range(reader_settings()['ahead']):
            await self.feed(self.gen)

    async def feed(self, gen):
        async with self.lock:
            if gen != self.gen or self.next >= len(self.items):
                if gen == self.gen and self.next >= len(self.items) and self.items:
                    await broadcast({'type': 'reader_end_queued'})
                return
            i = self.next
            self.next += 1
            item = self.items[i]
            if item.get('fetch'):  # matéria completa: baixa na hora
                try:
                    text = await asyncio.get_running_loop().run_in_executor(None, news.read_article, item['fetch'])
                except Exception as e:
                    text = f'Não consegui abrir a matéria: {e}'
                extra = reader.chunk([(0, text)])
                self.items[i:i + 1] = [{'t': item['t']}] + extra
                item = self.items[i]
            cfg = reader_settings()
            voice = None if cfg['voice'] == 'auto' else cfg['voice']
            try:
                audio = await asyncio.get_running_loop().run_in_executor(
                    TTS_POOL, lambda: synthesize(speakable(item['t']), voice=voice, pitch=cfg['pitch'] or None,
                                                 speed=cfg['speed'], tail_ms=cfg['pause_ms'] if item.get('end') else 80))
            except Exception as e:
                print('[leitor]', e, flush=True)
                return
            if gen != self.gen:
                return
            await broadcast({'type': 'reader_say', 'gen': gen, 'index': i, 'total': len(self.items),
                             'text': item['t'], 'page': item.get('p'), 'audio': base64.b64encode(audio).decode()})

    def stop(self):
        self.gen += 1

    def position(self, index):
        if self.source and self.source.get('doc_id'):
            reader.set_pos(self.source['doc_id'], index)


reading: Reader | None = None


def news_items(items, full=False):
    out = []
    for it in items:
        head = f"{it.get('fonte', '')}. {it.get('titulo', '')}."
        if full:
            out.append({'t': head, 'fetch': it.get('link'), 'end': True})
        else:
            out.append({'t': f"{head} {it.get('resumo', '')}".strip(), 'end': True})
    return out


async def handle_reader(data):
    kind = data.get('type')
    if kind == 'reader_play':
        if data.get('source') == 'doc':
            doc = reader.get(data.get('doc'))
            if not doc:
                return
            parts = await asyncio.get_running_loop().run_in_executor(None, reader.chunks_of, doc)
            start = data.get('index')
            if data.get('page'):
                start = reader.index_of_page(doc, int(data['page']))
            if start is None:
                start = doc.get('pos', 0) if doc.get('pos', 0) < len(parts) else 0
            await reading.start({'kind': 'doc', 'doc_id': doc['id'], 'title': doc['name']}, parts, int(start))
        elif data.get('source') == 'news':
            items = news_items(data.get('items') or [], full=bool(data.get('full')))
            title = data.get('title') or 'Notícias'
            await reading.start({'kind': 'news', 'title': title}, items, 0)
    elif kind == 'reader_need':
        if data.get('gen') == reading.gen:
            await reading.feed(reading.gen)
    elif kind == 'reader_pos':
        if data.get('gen') == reading.gen:
            reading.position(int(data.get('index', 0)))
    elif kind == 'reader_seek':
        if not reading.source:
            return
        index = int(data.get('index', 0))
        if reading.source['kind'] == 'doc' and data.get('page'):
            doc = reader.get(reading.source['doc_id'])
            index = reader.index_of_page(doc, int(data['page']))
        await reading.start(reading.source, reading.items, max(0, index))
    elif kind == 'reader_stop':
        reading.stop()


# ================================================================ central: arquivos, programas, integração
CENTRAL_FILES = STATE / 'central-files.json'
CENTRAL_PROJECTS = STATE / 'central-projects.json'
CENTRAL_APPS_AI = STATE / 'central-apps-ai.json'
CENTRAL_FILES_AI = STATE / 'central-files-ai.json'
FLOWS = STATE / 'flows.json'
SCAN_POOL = ThreadPoolExecutor(max_workers=2)
central_jobs: dict[str, dict] = {}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


async def ask_ai(prompt: str, timeout=600) -> str:
    """Pergunta avulsa ao Claude, sem ferramentas (análises e explicações da Central)."""
    cmd = ['claude', '-p', prompt, '--output-format', 'json', '--tools', '', '--strict-mcp-config',
           '--model', CONFIG.get('central_model', 'sonnet')]
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(STATE), env=claude_env(),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    d = json.loads(out or b'{}')
    if d.get('is_error'):
        raise RuntimeError(str(d.get('result'))[:200])
    return (d.get('result') or '').strip()


def json_in(text: str):
    """Primeiro objeto/lista JSON dentro de uma resposta."""
    m = re.search(r'```(?:json)?\s*(.+?)```', text, re.S)
    text = m.group(1) if m else text
    start = min([i for i in (text.find('{'), text.find('[')) if i != -1], default=-1)
    if start == -1:
        raise ValueError('sem JSON na resposta')
    return json.JSONDecoder().raw_decode(text[start:])[0]


async def cached_scan(path: Path, fn, refresh: bool, max_age: float):
    data = read_json(path, None)
    if refresh or not data or time.time() - data.get('_at', 0) > max_age:
        result = await asyncio.get_running_loop().run_in_executor(SCAN_POOL, fn)
        data = {'_at': time.time(), 'data': result}
        path.write_text(json.dumps(data, ensure_ascii=False))
    return data


@app.get('/api/central/files')
async def central_files(refresh: bool = False):
    d = await cached_scan(CENTRAL_FILES, central.scan_files, refresh, 6 * 3600)
    return {**d['data'], 'analise': read_json(CENTRAL_FILES_AI, None)}


@app.post('/api/central/files/analyze')
async def central_files_analyze():
    r = (await cached_scan(CENTRAL_FILES, central.scan_files, False, 6 * 3600))['data']
    h = central.human
    resumo = {
        'pastas': {k: f"{v['arquivos']} arquivos, {h(v['tamanho'])}, {v['recentes_30d']} mexidos em 30 dias" for k, v in r['pastas'].items()},
        'tipos': {k: f"{v['arquivos']} arquivos, {h(v['tamanho'])}" for k, v in r['tipos'].items()},
        'grandes': [f"{b['caminho'].replace(str(HOME), '~')} ({h(b['tamanho'])})" for b in r['grandes'][:12]],
        'duplicados': f"{r['duplicados_total']} grupos, {h(r['duplicados_espaco'])}",
        'nomes_de_copia': [Path(c).name for c in r['copias'][:25]],
        'pastas_vazias': r['vazias_total'], 'downloads_antigos': r['downloads_antigos'],
        'soltos_na_home': r['soltos_home'], 'area_de_trabalho': r['soltos_area_trabalho'],
    }
    try:
        top = await asyncio.get_running_loop().run_in_executor(None, lambda: sorted(
            (e.name for p in central.user_dirs().values() for e in os.scandir(p) if e.is_dir() and not e.name.startswith('.')))[:120])
    except Exception:
        top = []
    prompt = ('Você é um consultor de organização digital e produtividade. Abaixo está o retrato das pastas pessoais de um '
              'usuário Linux (perito forense e desenvolvedor, usa Obsidian como segundo cérebro). Escreva em português do Brasil '
              'uma análise prática em markdown simples (títulos ##, listas curtas), com: 1) diagnóstico em 3 linhas; '
              '2) as 5 melhorias de organização mais valiosas, cada uma com o passo concreto; 3) uma estrutura de pastas '
              'recomendada (árvore curta) adaptada ao que existe; 4) convenção de nomes; 5) 3 hábitos/automações de '
              'produtividade. Seja específico com os nomes reais. Não invente dados.\n\n'
              f'RETRATO: {json.dumps(resumo, ensure_ascii=False)}\n\nSUBPASTAS DE PRIMEIRO NÍVEL: {json.dumps(top, ensure_ascii=False)}')
    try:
        text = await ask_ai(prompt)
    except Exception as e:
        return JSONResponse({'error': f'Falha na análise: {e}'}, status_code=500)
    data = {'quando': time.time(), 'texto': text}
    CENTRAL_FILES_AI.write_text(json.dumps(data, ensure_ascii=False))
    return data


@app.get('/api/central/apps')
async def central_apps():
    loop = asyncio.get_running_loop()
    apps, pkgs = await asyncio.gather(loop.run_in_executor(SCAN_POOL, central.programs),
                                      loop.run_in_executor(SCAN_POOL, central.packages))
    return {'apps': apps, 'pacotes': pkgs, 'ia': read_json(CENTRAL_APPS_AI, {}),
            'job': central_jobs.get('apps')}


async def explain_apps(items: list[dict]):
    job = central_jobs['apps'] = {'feitos': 0, 'total': len(items), 'rodando': True, 'erro': None}
    await broadcast({'type': 'central', 'what': 'apps', 'job': job})
    batches = [items[i:i + 20] for i in range(0, len(items), 20)]
    sem = asyncio.Semaphore(2)

    async def one(batch):
        lista = [{'id': a['key'], 'nome': a['nome'], 'descricao': a.get('descricao', ''), 'tipo': a.get('tipo'),
                  'categorias': a.get('categorias') or a.get('secao'), 'comando': a.get('exec', '')} for a in batch]
        prompt = ('Explique cada programa instalado neste Linux (Ubuntu/Zorin) para o dono do computador, em português do '
                  'Brasil. Responda SOMENTE com um objeto JSON: chave = id exato; valor = {"faz": "o que é e para que '
                  'serve, 1-2 frases", "capacidades": ["3 a 5 coisas concretas que ele faz"], "dica": "um uso prático ou '
                  'atalho útil, 1 frase"}. Se não reconhecer um item, deduza pelo nome e descrição e diga que é provável.\n\n'
                  + json.dumps(lista, ensure_ascii=False))
        async with sem:
            try:
                got = json_in(await ask_ai(prompt))
            except Exception as e:
                job['erro'] = str(e)[:200]
                got = {}
        cache = read_json(CENTRAL_APPS_AI, {})
        for k, v in (got.items() if isinstance(got, dict) else []):
            if isinstance(v, dict):
                cache[k] = v
        CENTRAL_APPS_AI.write_text(json.dumps(cache, ensure_ascii=False))
        job['feitos'] += len(batch)
        await broadcast({'type': 'central', 'what': 'apps', 'job': job, 'ia': {k: cache[k] for k in got if k in cache}})

    await asyncio.gather(*(one(b) for b in batches))
    job['rodando'] = False
    await broadcast({'type': 'central', 'what': 'apps', 'job': job})


@app.post('/api/central/apps/explain')
async def central_apps_explain(req: Request):
    d = await req.json()
    if (central_jobs.get('apps') or {}).get('rodando'):
        return {'ok': False, 'erro': 'Já há uma explicação em andamento.'}
    items = [i for i in d.get('items', []) if i.get('key')][:400]
    if not d.get('refazer'):
        done = read_json(CENTRAL_APPS_AI, {})
        items = [i for i in items if i['key'] not in done]
    if not items:
        return {'ok': True, 'total': 0}
    asyncio.create_task(explain_apps(items))
    return {'ok': True, 'total': len(items)}


@app.post('/api/central/launch')
async def central_launch(req: Request):
    app_id = str((await req.json()).get('id', ''))
    if app_id not in {a['id'] for a in central.programs()}:
        return JSONResponse({'error': 'programa desconhecido'}, status_code=400)
    subprocess.Popen(['gtk-launch', app_id], env=claude_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'ok': True}


@app.post('/api/central/open')
async def central_open(req: Request):
    path = Path(str((await req.json()).get('path', ''))).expanduser().resolve()
    if not path.exists() or not str(path).startswith(str(HOME)):
        return JSONResponse({'error': 'caminho inválido'}, status_code=400)
    target = path if path.is_dir() else path.parent
    subprocess.Popen(['xdg-open', str(target)], env=claude_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'ok': True}


@app.get('/api/central/projects')
async def central_projects(refresh: bool = False):
    d = await cached_scan(CENTRAL_PROJECTS, central.projects, refresh, 3600)
    return {'projetos': d['data'], 'quando': d['_at']}


def all_flows():
    custom = read_json(FLOWS, [])
    scheduled = {i.get('flow'): i for i in load_agenda() if i.get('flow')}
    out = []
    for f in [*({**t, 'modelo': True} for t in central.FLOW_TEMPLATES), *custom]:
        a = scheduled.get(f['id'])
        out.append({**f, 'agendado': {'id': a['id'], 'when': a['when'], 'repeat': a.get('repeat')} if a else None})
    return out


def flow_prompt(f: dict) -> str:
    alvo = ''
    if f.get('pastas'):
        alvo = '\nPastas/projetos envolvidos: ' + ', '.join(f['pastas'])
    return f"[Fluxo de produtividade: {f['nome']}]\n{f['pedido']}{alvo}"


@app.get('/api/central/flows')
async def central_flows():
    return all_flows()


@app.post('/api/central/flows')
async def central_flow_add(req: Request):
    d = await req.json()
    nome, pedido = str(d.get('nome', '')).strip(), str(d.get('pedido', '')).strip()
    if not nome or not pedido:
        return JSONResponse({'error': 'Nome e instrução são obrigatórios.'}, status_code=400)
    flow = {'id': 'meu-' + uuid.uuid4().hex[:6], 'nome': nome[:80], 'icone': str(d.get('icone') or '⚡')[:4],
            'descricao': str(d.get('descricao', ''))[:300], 'pedido': pedido[:3000],
            'pastas': [str(p)[:300] for p in d.get('pastas', [])][:20], 'repetir': d.get('repetir') or None}
    flows = read_json(FLOWS, [])
    flows.append(flow)
    FLOWS.write_text(json.dumps(flows, ensure_ascii=False, indent=1))
    return flow


@app.delete('/api/central/flows/{flow_id}')
async def central_flow_del(flow_id: str):
    FLOWS.write_text(json.dumps([f for f in read_json(FLOWS, []) if f['id'] != flow_id], ensure_ascii=False, indent=1))
    items = [i for i in load_agenda() if i.get('flow') != flow_id]
    save_agenda(items)
    await broadcast({'type': 'agenda', 'items': items})
    return {'ok': True}


@app.post('/api/central/flows/{flow_id}/run')
async def central_flow_run(flow_id: str, req: Request):
    f = next((x for x in all_flows() if x['id'] == flow_id), None)
    if not f:
        return JSONResponse({'error': 'fluxo não encontrado'}, status_code=404)
    if (await req.json()).get('modo') == 'segundo-plano':
        asyncio.create_task(run_task(flow_prompt(f), origin=f"fluxo {f['nome']}"))
    else:
        await send_to_brain(flow_prompt(f))
    return {'ok': True}


@app.post('/api/central/flows/{flow_id}/schedule')
async def central_flow_schedule(flow_id: str, req: Request):
    d = await req.json()
    f = next((x for x in all_flows() if x['id'] == flow_id), None)
    if not f:
        return JSONResponse({'error': 'fluxo não encontrado'}, status_code=404)
    items = [i for i in load_agenda() if i.get('flow') != flow_id]
    if d.get('repetir'):
        hh, mm = (str(d.get('hora') or '08:00').split(':') + ['0'])[:2]
        when = datetime.now().replace(hour=int(hh) % 24, minute=int(mm) % 60, second=0, microsecond=0)
        if when.timestamp() < time.time():
            when += timedelta(days=1)
        while d['repetir'] == 'dias-uteis' and when.weekday() >= 5:
            when += timedelta(days=1)
        items.append({'id': uuid.uuid4().hex[:6], 'when': when.timestamp(), 'repeat': d['repetir'], 'kind': 'tarefa',
                      'text': flow_prompt(f), 'flow': flow_id})
    items.sort(key=lambda i: i['when'])
    save_agenda(items)
    await broadcast({'type': 'agenda', 'items': items})
    return {'ok': True}


@app.post('/api/central/flows/suggest')
async def central_flows_suggest():
    projs = (await cached_scan(CENTRAL_PROJECTS, central.projects, False, 3600))['data']
    files = (await cached_scan(CENTRAL_FILES, central.scan_files, False, 6 * 3600))['data']
    resumo = {
        'projetos': [{'nome': p['nome'], 'onde': p['pai'], 'tipos': p['tipos'], 'alteracoes': p.get('alteracoes'),
                      'nao_enviados': p.get('nao_enviados'), 'ultimo_commit': (p.get('ultimo_commit') or {}).get('msg')}
                     for p in projs[:30]],
        'pastas': {k: v['arquivos'] for k, v in files['pastas'].items()},
        'tipos': {k: v['arquivos'] for k, v in files['tipos'].items()},
        'fluxos_que_ja_existem': [f['nome'] for f in all_flows()],
    }
    prompt = ('Crie fluxos de produtividade avançados e personalizados para este usuário, que tem um assistente (MESTRE) '
              'capaz de: ler/mover arquivos (com confirmação), usar git e docker, Gmail, Google Agenda e Drive, '
              'navegador, notas no Obsidian, tarefas agendadas e resumos falados. Integre pastas, projetos e '
              'ferramentas reais listados abaixo; não repita os fluxos que já existem. Responda SOMENTE com uma lista '
              'JSON de 5 objetos: {"nome": curto, "icone": um emoji, "descricao": 1-2 frases do benefício, "pedido": '
              'instrução completa e específica para o assistente executar (com caminhos reais e cuidado de pedir '
              'confirmação antes de mover, apagar ou enviar), "pastas": [caminhos envolvidos], "repetir": '
              '"diario"|"dias-uteis"|"semanal"|null}.\n\n' + json.dumps(resumo, ensure_ascii=False))
    try:
        got = json_in(await ask_ai(prompt))
    except Exception as e:
        return JSONResponse({'error': f'Falha ao sugerir: {e}'}, status_code=500)
    return [g for g in (got if isinstance(got, list) else []) if isinstance(g, dict) and g.get('nome') and g.get('pedido')]


# ================================================================ portal NCFN (online)
PORTAL = portal.settings(CONFIG)
relay = portal.Relay()
portal_brain: Brain | None = None
portal_mouth: Mouth | None = None
_portal_hold = ''


def portal_prompt():
    parts = [(ROOT / 'persona_portal.md').read_text()]
    local = ROOT / 'persona_portal.local.md'
    if local.exists():
        parts.append(local.read_text())
    return '\n\n'.join(parts)


def portal_flags():
    # Dentro do portal o cérebro só enxerga o navegador do usuário, pelo ncfn-tela.
    return ['--permission-mode', 'bypassPermissions', '--tools', 'Bash', '--strict-mcp-config',
            '--disallowedTools', 'AskUserQuestion,EnterPlanMode,ExitPlanMode']


async def portal_broadcast(msg: dict):
    for ws in list(relay.clients):
        if msg.get('type') == 'say' and not relay.clients.get(ws, {}).get('voice'):
            continue
        try:
            await ws.send_json(msg)
        except Exception:
            relay.clients.pop(ws, None)


def portal_voice_on():
    return any(c.get('voice') for c in relay.clients.values())


async def on_portal_brain(kind, payload):
    global _portal_hold
    if kind == 'delta':
        if portal_voice_on():
            portal_mouth.feed(payload)
        text = _portal_hold + payload
        cut = text.rfind('[')
        if cut != -1 and ']' not in text[cut:] and len(text) - cut < 16:
            text, _portal_hold = text[:cut], text[cut:]
        else:
            _portal_hold = ''
        text = strip_tags(text)
        if text:
            await portal_broadcast({'type': 'delta', 'text': fix_address(text)})
    elif kind == 'flush':
        portal_mouth.flush()
        if _portal_hold:
            await portal_broadcast({'type': 'delta', 'text': fix_address(strip_tags(_portal_hold))})
            _portal_hold = ''
    elif kind == 'tool':
        await portal_broadcast({'type': 'tool', 'detail': describe_tool(payload).replace('ncfn-tela ', '')})
    elif kind == 'done':
        portal_mouth.flush()
        await portal_broadcast({'type': 'done', 'error': bool(payload and payload.get('error'))})


async def portal_ask(ws, text: str):
    global portal_brain
    text = text.strip()
    if not text:
        return
    if portal_brain is None:
        portal_brain = Brain(on_portal_brain, profile='portal')
        await portal_brain.start()
    portal_mouth.stop()
    page = relay.clients.get(ws, {}).get('page') or {}
    await portal_broadcast({'type': 'user', 'text': text})
    await portal_broadcast({'type': 'status', 'state': 'thinking'})
    if portal_brain.busy:
        await portal_brain.interrupt()
        await asyncio.sleep(0.3)
    where = f"[página aberta: {page.get('path', '?')} — {page.get('title', '')}]\n" if page else ''
    await portal_brain.ask(where + text)


def local_origin(origin: str | None) -> bool:
    port = CONFIG['port']
    return origin in (None, f'http://127.0.0.1:{port}', f'http://localhost:{port}')


@app.middleware('http')
async def same_origin_only(request, call_next):
    # Um site qualquer aberto no navegador não pode mandar ordens ao MESTRE local.
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not local_origin(request.headers.get('origin')):
        return JSONResponse({'error': 'origem não autorizada'}, status_code=403)
    return await call_next(request)


@app.post('/api/portal/cmd')
async def portal_cmd(req: Request):
    """Usado pelo `ncfn-tela`: executa um comando na janela do portal aberta."""
    if req.client and req.client.host != '127.0.0.1':
        return JSONResponse({'ok': False, 'erro': 'só local'}, status_code=403)
    data = await req.json()
    return await relay.run(str(data.get('cmd', '')), [str(a) for a in data.get('args', [])])


@app.get('/portal/health')
async def portal_health():
    return {'ok': True}


@app.websocket('/portal/ws')
async def portal_ws(ws: WebSocket):
    global portal_mouth
    origin = (ws.headers.get('origin') or '').rstrip('/')
    if ws.scope.get('mestre_remote'):
        # Pela internet: só com o token do Cloudflare Access e da origem do portal.
        email = portal.verify_access(ws.headers.get('cf-access-jwt-assertion', ''), PORTAL)
        if not PORTAL['enabled'] or not email or origin not in PORTAL['origins']:
            await ws.close(code=1008)
            return
    else:
        email = 'local'
        if origin and origin not in PORTAL['origins'] and not local_origin(origin):
            await ws.close(code=1008)
            return
    await ws.accept()
    if portal_mouth is None:
        portal_mouth = Mouth(send=portal_broadcast)
    relay.clients[ws] = {'email': email, 'page': None, 'active': time.time(), 'voice': False}
    loop = asyncio.get_running_loop()
    await ws.send_json({'type': 'hello', 'busy': bool(portal_brain and portal_brain.busy)})
    try:
        while True:
            msg = await ws.receive()
            if msg.get('type') == 'websocket.disconnect':
                break
            if msg.get('bytes') is not None:
                relay.touch(ws)
                await ws.send_json({'type': 'status', 'state': 'transcribing'})
                try:
                    text = await loop.run_in_executor(STT_POOL, transcribe, msg['bytes'])
                except Exception as e:
                    print('[stt portal]', e, flush=True)
                    text = ''
                if text:
                    await portal_ask(ws, text)
                else:
                    await ws.send_json({'type': 'status', 'state': 'idle'})
                continue
            if not msg.get('text'):
                continue
            data = json.loads(msg['text'])
            kind = data.get('type')
            if kind == 'page':
                relay.touch(ws, page={'path': str(data.get('path', ''))[:300], 'title': str(data.get('title', ''))[:200]})
            elif kind == 'voice':
                relay.touch(ws, voice=bool(data.get('on')))
                if not portal_voice_on():
                    portal_mouth.stop()
            elif kind == 'text':
                relay.touch(ws)
                await portal_ask(ws, str(data.get('text', ''))[:4000])
            elif kind == 'cmd_result':
                relay.answer(data)
            elif kind == 'stop':
                portal_mouth.stop()
                if portal_brain:
                    await portal_brain.interrupt()
            elif kind == 'reset':
                portal_mouth.stop()
                if portal_brain:
                    await portal_brain.restart(keep_session=False)
                await portal_broadcast({'type': 'reset'})
    except WebSocketDisconnect:
        pass
    finally:
        relay.clients.pop(ws, None)


# ================================================================ HTTP
@app.on_event('startup')
async def startup():
    global mouth, brain
    global reading
    mouth = Mouth()
    brain = Brain(on_brain)
    reading = Reader()
    loop = asyncio.get_running_loop()
    if CONFIG.get('stt') != 'openai':
        loop.run_in_executor(STT_POOL, whisper)
    loop.run_in_executor(TTS_POOL, synthesize, 'Pronto.')
    await brain.start()
    asyncio.create_task(agenda_loop())
    asyncio.create_task(watch_loop())
    asyncio.create_task(split_loop())
    if PORTAL['enabled']:
        asyncio.create_task(portal.serve_bridge(app, PORTAL, CONFIG['port']))


@app.get('/')
async def index():
    return FileResponse(ROOT / 'ui' / 'index.html')


def gpu_stats():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2).stdout
        u, used, total, temp = [float(x) for x in out.strip().split(',')]
        return {'util': u, 'mem': round(used / total * 100), 'temp': temp}
    except Exception:
        return None


_net = {'t': time.time(), 'rx': psutil.net_io_counters().bytes_recv, 'tx': psutil.net_io_counters().bytes_sent}


@app.get('/api/stats')
async def stats():
    now = time.time()
    io_ = psutil.net_io_counters()
    dt = max(0.5, now - _net['t'])
    rx, tx = (io_.bytes_recv - _net['rx']) / dt, (io_.bytes_sent - _net['tx']) / dt
    _net.update(t=now, rx=io_.bytes_recv, tx=io_.bytes_sent)
    try:
        containers = subprocess.run(['docker', 'ps', '-q'], capture_output=True, text=True, timeout=2).stdout.split()
    except Exception:
        containers = []
    return JSONResponse({
        'cpu': psutil.cpu_percent(), 'ram': psutil.virtual_memory().percent,
        'disk': psutil.disk_usage(str(HOME)).percent, 'gpu': gpu_stats(),
        'net': {'rx': rx, 'tx': tx}, 'containers': len(containers), 'uptime': now - psutil.boot_time(),
        'brain': 'online' if brain and brain.proc and brain.proc.returncode is None else 'offline',
        'model': CONFIG['model'], 'jobs': sum(1 for j in jobs.values() if j['status'] == 'rodando'),
        'agenda': len(load_agenda()),
    })


VOICE_CHOICES = {'masculino': {'pm_alex': 'Alex', 'pm_santa': 'Santa (encorpada)', 'piper-faber': 'Faber (Piper)'},
                 'feminino': {'pf_dora': 'Dora'}}


def public_settings():
    prof = voice_profile()
    return {'mode': mode, 'stt': CONFIG.get('stt', 'local'), 'gender': CONFIG.get('gender', 'masculino'),
            'voice': prof.get('voice'), 'pitch': prof.get('pitch', 1.0),
            'voice_choices': VOICE_CHOICES[CONFIG.get('gender', 'masculino')],
            'model': CONFIG['model'], 'watch': CONFIG.get('watch', True),
            'openai_key_set': bool(secrets().get('openai_api_key')), 'always': always_rules(),
            'theme': CONFIG.get('theme', 'auto'), 'rain': CONFIG.get('rain', True),
            'address_m': address_word('m'), 'address_f': address_word('f'), 'address_now': address,
            'address_pitch': round(last_pitch) if last_pitch else None,
            'ticker': CONFIG.get('ticker', True) and permissions.current().get('news', True),
            **memory_info(),
            'newsflash': news.db_path() is not None,
            'permissions': permissions.public()}


@app.get('/api/settings')
async def get_settings():
    return public_settings()


@app.post('/api/settings')
async def set_settings(req: Request):
    global mode
    data = await req.json()
    restart = False
    if data.get('mode') in ('free', 'wake', 'ptt'):
        mode = CONFIG['mode'] = data['mode']
    if data.get('stt') in ('local', 'openai'):
        CONFIG['stt'] = data['stt']
        if data['stt'] == 'local':
            asyncio.get_running_loop().run_in_executor(STT_POOL, whisper)
    if data.get('gender') in ('masculino', 'feminino') and data['gender'] != CONFIG.get('gender'):
        CONFIG['gender'] = data['gender']
        restart = True
    if data.get('model') in ('opus', 'sonnet', 'haiku') and data['model'] != CONFIG['model']:
        CONFIG['model'] = data['model']
        restart = True
    if isinstance(data.get('watch'), bool):
        CONFIG['watch'] = data['watch']
    if data.get('theme') in ('auto', 'ciano', 'verde', 'ambar', 'vermelho', 'roxo', 'rosa', 'branco'):
        CONFIG['theme'] = data['theme']
    if isinstance(data.get('rain'), bool):
        CONFIG['rain'] = data['rain']
    if isinstance(data.get('ticker'), bool):
        CONFIG['ticker'] = data['ticker']
    for voice in ADDRESS_DEFAULT:  # vazio volta ao padrão (MESTRE / DÍVA)
        word = data.get(f'address_{voice}')
        if isinstance(word, str):
            CONFIG[f'address_{voice}'] = re.sub(r'[\[\]\n\r\t]', ' ', word).strip()[:30]
    g = CONFIG.get('gender', 'masculino')
    if data.get('voice') in VOICE_CHOICES[g]:
        CONFIG['voices'][g]['voice'] = data['voice']
        CONFIG['voices'][g]['engine'] = 'piper' if data['voice'].startswith('piper') else 'kokoro'
    if isinstance(data.get('pitch'), (int, float)) and 0.7 <= data['pitch'] <= 1.2:
        CONFIG['voices'][g]['pitch'] = round(float(data['pitch']), 2)
    if isinstance(data.get('openai_api_key'), str):
        s = secrets()
        key = data['openai_api_key'].strip()
        if key:
            s['openai_api_key'] = key
        else:
            s.pop('openai_api_key', None)
        SECRETS.write_text(json.dumps(s))
        os.chmod(SECRETS, 0o600)
    if isinstance(data.get('permissions'), dict):
        before = permissions.disallowed_tools(permissions.current())
        perms = CONFIG.setdefault('permissions', {})
        for k, v in data['permissions'].items():
            if k in permissions.PERMISSIONS and isinstance(v, bool):
                perms[k] = v
        save_config()
        if permissions.disallowed_tools(permissions.current()) != before:
            restart = True  # ferramentas inteiras entram ou saem do cérebro
    if isinstance(data.get('memory_dir'), str):
        raw = data['memory_dir'].strip()
        if raw:
            target = Path(raw).expanduser()
            if not target.is_absolute():
                return JSONResponse({**public_settings(), 'error': 'Use um caminho completo (ex.: /home/... ou ~/...).'}, status_code=400)
            try:
                seed_memory(target)
            except OSError as e:
                return JSONResponse({**public_settings(), 'error': f'Não consegui usar essa pasta: {e}'}, status_code=400)
            raw = str(target)
        if raw != CONFIG.get('memory_dir', ''):
            CONFIG['memory_dir'] = raw
            restart = True  # o cérebro relê a memória do novo local
    if isinstance(data.get('remove_always'), str):
        ALWAYS.write_text(json.dumps([r for r in always_rules() if r['match'] != data['remove_always']],
                                     ensure_ascii=False, indent=1))
    save_config()
    if restart:
        # Não corta uma resposta no meio: reinicia quando ela terminar.
        if brain.busy:
            brain.restart_when_idle = True
        else:
            await brain.restart(keep_session=True)
    await broadcast({'type': 'settings', 'settings': public_settings()})
    return public_settings()


_connections = {'at': 0, 'data': {}}
CONNECTOR_NAMES = {'gmail': 'Gmail', 'calendar': 'Google Calendar', 'drive': 'Google Drive',
                   'docs': 'Claude Docs', 'cloudflare': 'Cloudflare Developer Platform', 'browser': 'playwright'}


@app.get('/api/connections')
async def connections(refresh: bool = False):
    """Estado dos conectores (claude mcp list), guardado por 2 minutos."""
    if refresh or time.time() - _connections['at'] > 120:
        try:
            proc = await asyncio.create_subprocess_exec('claude', 'mcp', 'list', cwd=str(HOME), env=claude_env(),
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            text = out.decode(errors='replace')
        except Exception:
            text = ''
        data = {}
        for key, name in CONNECTOR_NAMES.items():
            line = next((l for l in text.splitlines() if l.split(':')[0].strip().endswith(name)), '')
            data[key] = 'conectado' if '✔' in line or 'Connected' in line else \
                        'precisa entrar' if 'authentication' in line.lower() else \
                        'falhou' if '✗' in line or 'Failed' in line else 'desconhecido'
        _connections.update(at=time.time(), data=data)
    return _connections['data']


@app.post('/api/connect/{key}')
async def connect(key: str):
    """Conectar um serviço: o cérebro chama a autenticação do conector e abre o link."""
    if key == 'manage':
        subprocess.Popen(['xdg-open', 'https://claude.ai/settings/connectors'], env=claude_env())
        return {'ok': True}
    name = CONNECTOR_NAMES.get(key)
    if not name:
        return JSONResponse({'ok': False}, status_code=404)
    await send_to_brain(f'Conecte o {name}: chame a ferramenta de autenticação desse conector '
                        f'(a que termina em __authenticate) e abra o link que ela devolver no meu navegador com '
                        f'mestre-pc open. Depois me diga para fazer o login e avisar quando terminar.')
    _connections['at'] = 0
    return {'ok': True}


@app.get('/api/news')
async def news_headlines(refresh: bool = False):
    """Manchetes dos feeds do NewsFlash para a faixa de notícias."""
    if not permissions.current().get('news', True) or not news.db_path():
        return {'items': [], 'origem': 'desligado'}
    try:
        items, origem = await asyncio.get_running_loop().run_in_executor(None, news.headlines, None, None, 25, refresh)
    except Exception as e:
        return {'items': [], 'origem': f'erro: {e}'}
    return {'origem': origem, 'items': [{'titulo': i['titulo'], 'fonte': i.get('fonte', ''), 'link': i['link'],
                                         'categoria': i.get('categoria', ''), 'quando': news.when(i['data']),
                                         'resumo': i.get('resumo', '')}
                                        for i in items]}


@app.get('/api/library')
async def library_list():
    return reader.documents()


@app.post('/api/library/upload')
async def library_upload(req: Request):
    from urllib.parse import unquote
    name = unquote(req.headers.get('x-filename', 'documento.txt'))
    data = await req.body()
    if len(data) > 200 * 1024 * 1024:
        return JSONResponse({'error': 'Arquivo maior que 200 MB.'}, status_code=413)
    try:
        doc = await asyncio.get_running_loop().run_in_executor(None, reader.add, name, data)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    await broadcast({'type': 'library', 'items': reader.documents()})
    return doc


@app.delete('/api/library/{doc_id}')
async def library_remove(doc_id: str):
    if reading.source and reading.source.get('doc_id') == doc_id:
        reading.stop()
    reader.remove(doc_id)
    await broadcast({'type': 'library', 'items': reader.documents()})
    return {'ok': True}


@app.post('/api/library/{doc_id}/ask')
async def library_ask(doc_id: str, req: Request):
    """Entrega o documento ao cérebro: ler, resumir ou responder perguntas."""
    doc = reader.get(doc_id)
    if not doc:
        return JSONResponse({'ok': False}, status_code=404)
    q = ((await req.json()) or {}).get('question') or 'Leia este documento e me diga em poucas frases do que se trata.'
    await send_to_brain(f'Documento da biblioteca: "{doc["name"]}" ({doc["pages"]} páginas). Arquivo original: '
                        f'{doc["path"]}. Texto extraído (com marcações de página): {doc["text"]}. '
                        f'Use o texto extraído (leia em partes se for grande). Pedido: {q}')
    return {'ok': True}


@app.get('/api/reader/settings')
async def reader_get():
    return reader_settings()


@app.post('/api/reader/settings')
async def reader_set(req: Request):
    d = await req.json()
    cfg = CONFIG.setdefault('reader', {})
    if isinstance(d.get('speed'), (int, float)) and 0.5 <= d['speed'] <= 2:
        cfg['speed'] = round(float(d['speed']), 2)
    if isinstance(d.get('pitch'), (int, float)) and (d['pitch'] == 0 or 0.7 <= d['pitch'] <= 1.2):
        cfg['pitch'] = round(float(d['pitch']), 2)
    if isinstance(d.get('pause_ms'), int) and 0 <= d['pause_ms'] <= 3000:
        cfg['pause_ms'] = d['pause_ms']
    if d.get('voice') in ('auto', 'pm_alex', 'pm_santa', 'pf_dora', 'piper-faber'):
        cfg['voice'] = d['voice']
    save_config()
    return reader_settings()


@app.post('/api/memory/pick')
async def memory_pick():
    """Abre o seletor de pastas do sistema e devolve a escolhida (sem salvar)."""
    start = CONFIG.get('memory_dir') or str(HOME / 'Documentos')
    proc = await asyncio.create_subprocess_exec(
        'zenity', '--file-selection', '--directory', '--title=Pasta da memória do MESTRE (arquivos .md)',
        f'--filename={Path(start).expanduser()}/', env=claude_env(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return {'path': out.decode().strip()}


@app.post('/api/memory/open')
async def memory_open():
    d = Path(CONFIG.get('memory_dir') or '').expanduser()
    if d.is_dir():
        subprocess.Popen(['xdg-open', str(d)], env=claude_env())
    return {'ok': d.is_dir()}


@app.get('/api/agenda')
async def agenda_list():
    return load_agenda()


@app.post('/api/agenda')
async def agenda_add(req: Request):
    d = await req.json()
    item = {'id': uuid.uuid4().hex[:6], 'when': float(d['when']), 'repeat': d.get('repeat') or None,
            'kind': 'tarefa' if d.get('kind') == 'tarefa' else 'lembrete', 'text': d['text']}
    items = load_agenda()
    items.append(item)
    items.sort(key=lambda i: i['when'])
    save_agenda(items)
    await broadcast({'type': 'agenda', 'items': items})
    return item


@app.delete('/api/agenda/{item_id}')
async def agenda_del(item_id: str):
    items = [i for i in load_agenda() if i['id'] != item_id]
    save_agenda(items)
    await broadcast({'type': 'agenda', 'items': items})
    return {'ok': True}


@app.post('/api/task')
async def task_new(req: Request):
    d = await req.json()
    asyncio.create_task(run_task(d['text']))
    return {'ok': True}


@app.get('/api/tasks')
async def task_list():
    return list(jobs.values())


@app.post('/api/window/{action}')
async def window(action: str):
    pc = str(ROOT / 'bin' / 'mestre-pc')
    env = claude_env()
    if action == 'minimize':
        subprocess.Popen([pc, 'minimize', 'MESTRE'], env=env)
    elif action == 'maximize':
        subprocess.Popen([pc, 'fill', 'MESTRE', '--toggle'], env=env)
    elif action == 'off':
        subprocess.Popen([str(HOME / '.local' / 'bin' / 'mestre'), 'stop'], start_new_session=True)
    elif action == 'close':
        # Com o portal ligado, o ✕ só fecha a janela: o assistente segue disponível online.
        how = 'fechar' if PORTAL['enabled'] and PORTAL['keep_alive'] else 'stop'
        subprocess.Popen([str(HOME / '.local' / 'bin' / 'mestre'), how], start_new_session=True)
    return {'ok': True}


@app.websocket('/ws')
async def ws_endpoint(ws: WebSocket):
    if not local_origin(ws.headers.get('origin')):
        await ws.close(code=1008)
        return
    await ws.accept()
    clients.add(ws)
    loop = asyncio.get_running_loop()
    barge = None  # trecho que interrompeu a fala: frases recentes do MESTRE
    await ws.send_json({'type': 'hello', 'settings': public_settings(), 'agenda': load_agenda(),
                        'tasks': list(jobs.values())})
    try:
        while True:
            msg = await ws.receive()
            if msg.get('type') == 'websocket.disconnect':
                break
            if msg.get('bytes') is not None:
                await broadcast({'type': 'status', 'state': 'transcribing'})
                try:
                    text = await loop.run_in_executor(STT_POOL, transcribe, msg['bytes'])
                except Exception as e:
                    print('[stt]', e, flush=True)
                    text = ''
                recent, barge = barge, None
                if text and recent is None:  # no trecho da interrupção pode vir o eco da voz do MESTRE
                    await loop.run_in_executor(STT_POOL, update_address, msg['bytes'])
                if recent is not None:
                    if STOP_WORDS.fullmatch(text.strip()):
                        print(f'[barge] parar: {text}', flush=True)
                        mouth.stop()
                        await brain.interrupt()
                        await broadcast({'type': 'barge_stop'})
                        await broadcast({'type': 'status', 'state': 'idle'})
                        continue
                    if not text or is_echo(text, recent):
                        print(f'[barge] ignorado: {text or "(sem fala)"}', flush=True)
                        await ws.send_json({'type': 'barge_rejected'})
                        continue
                if not text:
                    await broadcast({'type': 'status', 'state': 'idle'})
                    continue
                await handle_utterance(text, 'voice')
                continue
            if not msg.get('text'):
                continue
            data = json.loads(msg['text'])
            kind = data.get('type')
            if kind == 'barge':
                barge = [str(t)[:500] for t in (data.get('recent') or [])[:4]]
            elif kind == 'text':
                await handle_utterance(data.get('text', ''), 'typed')
            elif kind and kind.startswith('reader_'):
                await handle_reader(data)
            elif kind == 'decision':
                await decide_pending(data.get('choice', 'deny'))
            elif kind == 'stop':
                mouth.stop()
                await brain.interrupt()
            elif kind == 'reset':
                mouth.stop()
                await brain.restart(keep_session=False)
                await broadcast({'type': 'reset'})
            elif kind == 'test_voice':
                mouth.stop()
                mouth.say(f'Teste de voz. É assim que eu soo agora, {address_word()}.')
            elif kind == 'greet':
                await broadcast({'type': 'emotion', 'name': 'sorriso'})
                greeting = CONFIG['greeting_f' if CONFIG.get('gender') == 'feminino' else 'greeting']
                if address == 'f':
                    greeting = greeting.replace('Bem-vindo', 'Bem-vinda')
                mouth.say(greeting.replace('{tratamento}', address_word()))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
