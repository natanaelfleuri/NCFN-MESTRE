"""Permissões do MESTRE: o que o cérebro pode usar, ligado ou desligado em
Configurações. Lidas pelo servidor (ferramentas inteiras ficam de fora do
Claude) e pelo guardião (comandos específicos são recusados)."""
import json
import re
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent / 'config.json'

# key: (grupo, rótulo, descrição, padrão, ferramentas a bloquear, padrão de comando Bash a bloquear)
PERMISSIONS = {
    'gmail': ('Conexões', 'Gmail', 'Ler, buscar, escrever rascunhos e enviar e-mails (enviar pede confirmação).',
              True, ['mcp__claude_ai_Gmail'], None),
    'calendar': ('Conexões', 'Google Agenda', 'Ver e criar compromissos (criar pede confirmação).',
                 True, ['mcp__claude_ai_Google_Calendar'], None),
    'drive': ('Conexões', 'Google Drive', 'Buscar e ler arquivos do Drive.',
              True, ['mcp__claude_ai_Google_Drive'], None),
    'docs': ('Conexões', 'Claude Docs', 'Criar e editar documentos no claude.ai.',
             True, ['mcp__claude_ai_Claude_Docs'], None),
    'cloudflare': ('Conexões', 'Cloudflare', 'Consultar a conta Cloudflare (túnel, DNS, Workers).',
                   True, ['mcp__claude_ai_Cloudflare_Developer_Platform'], None),
    'web': ('Internet', 'Pesquisa na internet', 'Buscar e ler páginas (WebSearch e WebFetch).',
            True, ['WebSearch', 'WebFetch'], None),
    'browser': ('Internet', 'Navegador automático', 'Abrir sites, clicar e preencher formulários (Playwright).',
                True, ['mcp__playwright'], None),
    'camera': ('Dispositivos', 'Câmera', 'Tirar foto pela webcam para enxergar o ambiente.',
               False, [], r'mestre-pc\s+camera'),
    'screen': ('Dispositivos', 'Ver a tela', 'Capturar a tela para entender o que está aberto.',
               True, [], r'mestre-pc\s+screenshot|\b(gnome-screenshot|scrot|import\s+-window)\b'),
    'control': ('Dispositivos', 'Mouse e teclado', 'Clicar, digitar, usar atalhos e mexer nas janelas.',
                True, [], r'mestre-pc\s+(click|move|scroll|type|key|focus|fullscreen|minimize)'),
    'apps': ('Dispositivos', 'Abrir programas e sites', 'Abrir aplicativos, arquivos e links no seu navegador.',
             True, [], r'mestre-pc\s+open|\b(xdg-open|gtk-launch|gio\s+open)\b'),
    'clipboard': ('Dispositivos', 'Área de transferência', 'Ler e copiar texto (Ctrl+C / Ctrl+V).',
                  True, [], r'mestre-pc\s+clipboard'),
    'media': ('Dispositivos', 'Som e mídia', 'Mudar o volume e controlar música e vídeos.',
              True, [], r'mestre-pc\s+(volume|media)|\b(wpctl|pactl|amixer)\s+set'),
    'terminal': ('Sistema', 'Terminal', 'Rodar comandos no computador (base de quase tudo; desligar limita muito).',
                 True, ['Bash'], None),
    'files': ('Sistema', 'Criar e editar arquivos', 'Escrever arquivos (fora de Documentos/MESTRE pede confirmação).',
              True, ['Write', 'Edit', 'NotebookEdit'], None),
    'docker': ('Sistema', 'Docker', 'Ver e gerenciar containers (parar e apagar pedem confirmação).',
               True, [], r'\bdocker\b'),
    'ncfn': ('Sistema', 'Áreas protegidas', 'Acessar as pastas de protected_paths (portal, cofre…), sempre com confirmação.',
             True, [], 'PROTECTED_PATHS'),
    'agenda': ('Autonomia', 'Agenda e lembretes', 'Criar lembretes e tarefas agendadas.',
               True, [], r'mestre-agenda'),
    'tasks': ('Autonomia', 'Tarefas em segundo plano', 'Executar trabalhos longos sozinho e avisar ao terminar.',
              True, [], r'mestre-tarefa'),
    'memory': ('Autonomia', 'Memória da conversa', 'Lembrar a conversa entre aberturas do MESTRE.',
               True, [], None),
}


def current() -> dict:
    try:
        saved = json.loads(CONFIG_FILE.read_text()).get('permissions', {})
    except (OSError, ValueError):
        saved = {}
    return {k: bool(saved.get(k, v[3])) for k, v in PERMISSIONS.items()}


def disallowed_tools(perms: dict) -> list[str]:
    tools = []
    for k, (_, _, _, _, blocked, _) in PERMISSIONS.items():
        if not perms.get(k, True):
            tools += blocked
    return tools


def protected_pattern() -> str | None:
    try:
        paths = json.loads(CONFIG_FILE.read_text()).get('protected_paths', [])
    except (OSError, ValueError):
        paths = []
    parts = [re.escape(Path(p).name if '/' in p else p) for p in paths if p]
    return '|'.join(parts) or None


def blocked_reason(tool: str, tool_input: dict, perms: dict) -> str | None:
    """Rótulo da permissão desligada que barra esta chamada, ou None."""
    command = str(tool_input.get('command', '')) if tool == 'Bash' else ''
    path = str(tool_input.get('file_path', '') or '')
    for k, (_, label, _, _, tools, pattern) in PERMISSIONS.items():
        if perms.get(k, True):
            continue
        if any(tool == t or tool.startswith(t + '__') for t in tools):
            return label
        if pattern == 'PROTECTED_PATHS':
            pattern = protected_pattern()
        if pattern and (re.search(pattern, command) or (k == 'ncfn' and re.search(pattern, path))):
            return label
    return None


def public() -> list[dict]:
    perms = current()
    return [{'key': k, 'group': g, 'label': label, 'desc': desc, 'on': perms[k]}
            for k, (g, label, desc, *_rest) in PERMISSIONS.items()]
