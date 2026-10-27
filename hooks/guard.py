#!/usr/bin/env python3
"""Guardião do MESTRE: gancho PreToolUse do Claude Code.

Libera leitura, navegação e automações comuns. O que é perigoso (apagar,
sudo, instalar, mexer no portal NCFN, no cofre ou no Segundo Cérebro, ler
segredos, enviar dados para fora) é recusado com o pedido de confirmação por
voz. Quando o usuário diz "confirmo", o servidor grava a aprovação daquela
ação exata (approved.json) e a mesma chamada passa uma única vez.
"""
import hashlib
import json
import os
import re
import sys
import time

HOME = os.path.expanduser('~')
STATE = os.environ.get('MESTRE_STATE', os.path.join(HOME, 'mestre', 'state'))
PENDING = os.path.join(STATE, 'pending.json')
APPROVED = os.path.join(STATE, 'approved.json')
ALWAYS = os.path.join(STATE, 'always.json')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(HOME, 'Documentos') if os.path.isdir(os.path.join(HOME, 'Documentos')) else os.path.join(HOME, 'Documents')
WORKSPACE = os.path.join(DOCS, 'MESTRE')

# Caminhos que só se tocam com confirmação (leitura de segredos inclusive).
PROTECTED = [
    os.path.join(HOME, '.ssh'),
    os.path.join(HOME, '.gnupg'),
    '.git-credentials',
    '.env',
    'ENVIRONMENT',
    'credentials',
    'id_rsa',
    'id_ed25519',
    '/etc/',
    os.path.join(HOME, '.config', 'gh'),
    os.path.join(ROOT, 'state'),
]
# Áreas extras do usuário (config.json → protected_paths), ex.: projetos e cofres.
try:
    PROTECTED += [os.path.expanduser(p) for p in json.load(open(os.path.join(ROOT, 'config.json'))).get('protected_paths', [])]
except (OSError, ValueError):
    pass

DANGEROUS_BASH = [
    (r'(^|[;&|`(]\s*)sudo\b|\bsu\s+-?\w*\s*$|\bpkexec\b', 'usar privilégios de administrador'),
    (r'(^|[;&|`(\s])(rm|rmdir|shred|unlink|srm)\s', 'apagar arquivos'),
    (r'(^|[;&|`(\s])mv\s', 'mover ou renomear arquivos'),
    (r'\b(dd|mkfs[.\w]*|fdisk|parted|wipefs|mkswap)\b', 'mexer em discos'),
    (r'\b(chmod|chown|chattr|setfacl)\b', 'mudar permissões'),
    (r'\b(kill|pkill|killall|xkill)\b', 'encerrar processos'),
    (r'\b(reboot|shutdown|poweroff|halt|systemctl\s+(suspend|hibernate))\b', 'desligar ou reiniciar o PC'),
    (r'\bsystemctl\s+(--user\s+)?(start|stop|restart|enable|disable|mask|daemon-reload)\b', 'controlar serviços'),
    (r'\b(apt|apt-get|dpkg|snap|flatpak)\s+(install|remove|purge|upgrade|dist-upgrade|autoremove|-i|-r|-P)\b', 'instalar ou remover programas'),
    (r'\b(pip3?|pipx|npm|pnpm|yarn|cargo|gem)\s+(install|uninstall|remove|i|add)\b', 'instalar ou remover pacotes'),
    (r'\bdocker\b.*\b(rm|rmi|stop|kill|down|prune|restart|volume\s+rm|system\s+prune|exec)\b', 'mexer em containers'),
    (r'\bgit\s+(push|reset\s+--hard|clean|rebase|filter-branch|branch\s+-D|checkout\s+--\s|restore)\b', 'alterar repositórios git'),
    (r'\bcrontab\s+-(e|r)|\bcrontab\s+\S+\s*$', 'mudar tarefas agendadas'),
    (r'\b(curl|wget|http)\b.*(\s-X\s*(POST|PUT|DELETE|PATCH)|\s(-d|--data[\w-]*|-F|--form|-T|--upload-file)\s)', 'enviar dados pela internet'),
    (r'\b(scp|rsync|sftp|ssh|nc|ncat|socat|ftp)\s', 'conectar a outra máquina'),
    (r'(^|[;&|`(]\s*)(mail|mailx|sendmail|mutt|msmtp)\s', 'enviar e-mail'),
    (r'(^|[^>&0-9])>\s*(?!/dev/null|/tmp/|&)', 'sobrescrever arquivos'),
    (r'\btruncate\b|\bsed\s+-i\b|\bperl\s+-i\b|\btee\b(?!\s+/tmp/)', 'alterar arquivos'),
    (r'\bpasswd\b|\busermod\b|\buseradd\b|\bvisudo\b', 'mexer em contas de usuário'),
    (r'\b(xdg-settings|gsettings\s+set|dconf\s+write)\b', 'mudar configurações do sistema'),
]

# Ferramentas de MCP que agem para fora (e-mail, agenda, documentos, envios).
OUTWARD_MCP = re.compile(r'(send|create|delete|update|upload|publish|post|remove|write|batch|draft)', re.I)
SAFE_PLAYWRIGHT = re.compile(r'^mcp__playwright__')


def decide(decision, reason=''):
    out = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': decision}}
    if reason:
        out['hookSpecificOutput']['permissionDecisionReason'] = reason
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


def touches_protected(text):
    for p in PROTECTED:
        if p in text:
            return p
    return None


def memory_dir():
    try:
        d = json.load(open(os.path.join(ROOT, 'config.json'))).get('memory_dir') or ''
    except (OSError, ValueError):
        return None
    d = os.path.expanduser(d)
    return d if d and os.path.isdir(d) else None


def in_memory(path):
    mem = memory_dir()
    return bool(mem and path and os.path.abspath(os.path.expanduser(path)).startswith(mem))


def danger(tool, ti):
    """Motivo para pedir confirmação, ou None quando a ação é segura."""
    if tool == 'Bash':
        cmd = str(ti.get('command', ''))
        for pattern, why in DANGEROUS_BASH:
            if re.search(pattern, cmd):
                return why
        mem = memory_dir()
        p = touches_protected(cmd.replace(mem, '') if mem else cmd)
        if p:
            return f'tocar em área protegida ({os.path.basename(p.rstrip("/")) or p})'
        return None
    if tool in ('Write', 'Edit', 'NotebookEdit', 'MultiEdit'):
        path = os.path.abspath(os.path.expanduser(str(ti.get('file_path') or ti.get('notebook_path') or '')))
        if memory_dir() and path.startswith(os.path.join(HOME, '.claude')):
            return 'MEMORIA_ERRADA'
        if in_memory(path):
            return None  # memória do assistente: escreve sem perguntar
        if touches_protected(path):
            return 'alterar arquivo em área protegida'
        if path.startswith(WORKSPACE) or path.startswith('/tmp/'):
            return None
        return f'alterar o arquivo {path}'
    if tool == 'Read':
        path = str(ti.get('file_path', ''))
        if in_memory(path):
            return None
        p = touches_protected(path)
        if p and not path.endswith(('.md', '.txt', '.pdf', '.png', '.jpg')):
            return 'ler um arquivo sensível'
        return None
    if tool.startswith('mcp__'):
        if SAFE_PLAYWRIGHT.match(tool):
            if tool.endswith(('file_upload', 'run_code_unsafe')):
                return 'enviar arquivo ou rodar código no navegador'
            return None
        if OUTWARD_MCP.search(tool.split('__')[-1]):
            return 'agir em um serviço externo (e-mail, agenda, documentos)'
    return None


def fingerprint(tool, ti):
    return hashlib.sha256(json.dumps([tool, ti], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        decide('allow')
    tool = data.get('tool_name', '')
    ti = data.get('tool_input') or {}
    # Permissões desligadas em Configurações: recusa sem oferecer confirmação.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
    try:
        import permissions
        off = permissions.blocked_reason(tool, ti, permissions.current())
    except Exception:
        off = None
    if off:
        decide('deny', f'PERMISSÃO DESLIGADA: o usuário desativou "{off}" em Configurações. Não tente contornar '
                       f'por outro caminho. Diga em uma frase que essa permissão está desligada e que ele pode '
                       f'ligá-la em Configurações, Conexões e permissões.')
    why = danger(tool, ti)
    if not why:
        decide('allow')
    if why == 'MEMORIA_ERRADA':
        decide('deny', f'Não use a memória interna do Claude Code (~/.claude). A memória do MESTRE é a pasta '
                       f'{memory_dir()} (Obsidian): grave lá com Write/Edit, seguindo as regras da seção Memória.')
    fp = fingerprint(tool, ti)
    match = str(ti.get('command') or ti.get('file_path') or ti.get('notebook_path') or json.dumps(ti, ensure_ascii=False))
    # "Confirmar sempre": a mesma ação exata passa sem perguntar de novo.
    try:
        for rule in json.load(open(ALWAYS)):
            if rule.get('tool') == tool and rule.get('match') == match:
                decide('allow', 'Permitido sempre pelo usuário.')
    except (OSError, ValueError):
        pass
    if os.environ.get('MESTRE_AUTONOMOUS'):
        decide('deny', f'AÇÃO PROTEGIDA ({why}) negada: tarefa autônoma, sem ninguém para confirmar. '
                       f'Siga sem ela e informe no resumo que ficou pendente.')
    os.makedirs(STATE, exist_ok=True)
    try:
        approved = json.load(open(APPROVED))
        if approved.get('fp') == fp and approved.get('expires', 0) > time.time():
            os.remove(APPROVED)
            decide('allow', 'Confirmado por voz pelo usuário.')
    except (OSError, ValueError):
        pass
    summary = ti.get('command') or ti.get('file_path') or json.dumps(ti, ensure_ascii=False)[:200]
    json.dump({'fp': fp, 'tool': tool, 'why': why, 'summary': str(summary)[:300], 'match': match, 'at': time.time()},
              open(PENDING, 'w'), ensure_ascii=False)
    decide('deny',
           f'AÇÃO PROTEGIDA ({why}). Não foi executada. Explique ao usuário, em uma frase curta, '
           f'o que pretende fazer e pergunte se ele confirma. Só quando ele disser que confirma, '
           f'repita EXATAMENTE a mesma chamada, sem mudar nada. Se ele recusar, desista.')


if __name__ == '__main__':
    main()
