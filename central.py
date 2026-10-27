"""Central do MESTRE: arquivos, programas instalados e projetos.

Varre as pastas pessoais (tamanho por tipo, arquivos grandes, duplicados,
downloads esquecidos, nomes de cópia…) e gera sugestões de organização;
lista os programas instalados (atalhos .desktop, Flatpak, Snap e pacotes apt
instalados manualmente) com a descrição de cada um; encontra projetos
(git, Node, Python, Docker, cofres do Obsidian) para os fluxos de
produtividade. Tudo só leitura: as ações passam pelo MESTRE, com o guardião.
"""
import configparser
import hashlib
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

HOME = Path.home()
SKIP_DIRS = {'node_modules', '.git', '.venv', 'venv', 'env', '__pycache__', '.cache', '.npm', '.cargo', 'site-packages',
             '.next', 'dist', 'build', '.tox', '.mypy_cache', 'snap', '.local', '.var', '.config', '.mozilla',
             '.pinokio', 'Trash', '.Trash-1000', 'cache', 'Cache', 'CACHE'}
CATEGORIES = {
    'Documentos': {'.pdf', '.doc', '.docx', '.odt', '.rtf', '.txt', '.md', '.xls', '.xlsx', '.ods', '.csv', '.ppt',
                   '.pptx', '.odp', '.epub'},
    'Imagens': {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tif', '.tiff', '.heic', '.raw', '.psd'},
    'Vídeos': {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.m4v'},
    'Áudio': {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus'},
    'Compactados': {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.zst'},
    'Instaladores': {'.deb', '.rpm', '.appimage', '.exe', '.msi', '.iso', '.img', '.run', '.flatpakref', '.apk'},
    'Máquinas virtuais e discos': {'.vdi', '.vmdk', '.vhd', '.vhdx', '.qcow2', '.ova', '.ovf', '.e01', '.ex01', '.dd',
                                   '.aff', '.vmem', '.dmp', '.mem'},
    'Mapas e GIS': {'.shp', '.dbf', '.shx', '.prj', '.qgz', '.qgs', '.qlr', '.kml', '.kmz', '.geojson', '.gpkg'},
    'Código': {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json', '.sh', '.c', '.cpp', '.h', '.java',
               '.go', '.rs', '.rb', '.php', '.sql', '.yml', '.yaml', '.toml', '.ipynb'},
}
EXT_CAT = {e: c for c, exts in CATEGORIES.items() for e in exts}
COPY_NAME = re.compile(r'(\(\d+\)|\bc[óo]pia\b|\bcopy\b|sem t[íi]tulo|untitled|\bnovo documento\b|_final\d*|final ?\(\d\))', re.I)


def user_dirs() -> dict[str, Path]:
    """Pastas pessoais do XDG (Documentos, Downloads, Área de trabalho…)."""
    dirs = {}
    for key in ('DESKTOP', 'DOWNLOAD', 'DOCUMENTS', 'PICTURES', 'VIDEOS', 'MUSIC', 'TEMPLATES', 'PUBLICSHARE'):
        try:
            p = Path(subprocess.run(['xdg-user-dir', key], capture_output=True, text=True, timeout=5).stdout.strip())
        except Exception:
            continue
        if p.is_dir() and p != HOME:
            dirs[p.name] = p
    return dirs


def human(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
        n /= 1024


def _walk(root: Path, deadline: float, limit: int):
    stack = [root]
    seen = 0
    while stack and time.time() < deadline and seen < limit:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        files = 0
        for e in entries:
            if e.name.startswith('.') and e.name != '.obsidian':
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    files += 1
                    seen += 1
                    yield e
            except OSError:
                continue
        if not files and not any(True for x in entries if not x.name.startswith('.')):
            yield ('empty', d)


def _quick_hash(path: str, size: int) -> str:
    h = hashlib.sha1(str(size).encode())
    with open(path, 'rb') as f:
        h.update(f.read(65536))
        if size > 131072:
            f.seek(size - 65536)
            h.update(f.read(65536))
    return h.hexdigest()


def scan_files(seconds=40, limit=400_000) -> dict:
    """Retrato das pastas pessoais, com sugestões."""
    now = time.time()
    deadline = now + seconds
    roots = user_dirs()
    folders, by_cat, big, copies, empties = {}, defaultdict(lambda: [0, 0]), [], [], []
    by_size = defaultdict(list)
    old_downloads, installers = [0, 0], []
    download = next((p for n, p in roots.items() if p.name.lower() in ('downloads', 'transferências')), None)
    desktop = next((p for p in roots.values() if 'rea de trabalho' in p.name or p.name.lower() == 'desktop'), None)
    for name, root in roots.items():
        count = size = recent = 0
        latest = 0
        for e in _walk(root, deadline, limit):
            if isinstance(e, tuple):
                if e[1] != root:
                    empties.append(str(e[1]))
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            count += 1
            size += st.st_size
            latest = max(latest, st.st_mtime)
            if now - st.st_mtime < 30 * 86400:
                recent += 1
            ext = os.path.splitext(e.name)[1].lower()
            cat = EXT_CAT.get(ext, 'Outros')
            by_cat[cat][0] += 1
            by_cat[cat][1] += st.st_size
            if st.st_size > 50 * 1024 * 1024:
                big.append({'caminho': e.path, 'tamanho': st.st_size, 'mod': st.st_mtime})
            if st.st_size > 512 * 1024:
                by_size[st.st_size].append(e.path)
            if COPY_NAME.search(e.name):
                copies.append(e.path)
            if download and e.path.startswith(str(download) + os.sep):
                if now - max(st.st_mtime, st.st_atime) > 90 * 86400:
                    old_downloads[0] += 1
                    old_downloads[1] += st.st_size
                if cat == 'Instaladores':
                    installers.append({'caminho': e.path, 'tamanho': st.st_size})
        folders[name] = {'caminho': str(root), 'arquivos': count, 'tamanho': size, 'recentes_30d': recent,
                         'ultima_mudanca': latest}
    loose_home = [e.name for e in os.scandir(HOME) if e.is_file() and not e.name.startswith('.')]
    loose_desktop = [e.name for e in os.scandir(desktop) if not e.name.endswith('.desktop')] if desktop else []
    dups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        groups = defaultdict(list)
        for p in paths[:20]:
            try:
                groups[_quick_hash(p, size)].append(p)
            except OSError:
                pass
        dups += [{'tamanho': size, 'arquivos': g} for g in groups.values() if len(g) > 1]
        if time.time() > deadline + 15:
            break
    dups.sort(key=lambda d: d['tamanho'] * (len(d['arquivos']) - 1), reverse=True)
    big.sort(key=lambda b: b['tamanho'], reverse=True)
    report = {
        'quando': now, 'incompleto': time.time() >= deadline,
        'pastas': folders,
        'tipos': {c: {'arquivos': v[0], 'tamanho': v[1]} for c, v in sorted(by_cat.items(), key=lambda kv: -kv[1][1])},
        'grandes': big[:25],
        'duplicados': dups[:40], 'duplicados_total': len(dups),
        'duplicados_espaco': sum(d['tamanho'] * (len(d['arquivos']) - 1) for d in dups),
        'copias': copies[:60], 'copias_total': len(copies),
        'vazias': empties[:60], 'vazias_total': len(empties),
        'downloads_antigos': {'arquivos': old_downloads[0], 'tamanho': old_downloads[1]},
        'instaladores': sorted(installers, key=lambda i: -i['tamanho'])[:30],
        'soltos_home': loose_home[:40], 'soltos_area_trabalho': loose_desktop[:40],
        'download_dir': str(download) if download else '', 'desktop_dir': str(desktop) if desktop else '',
    }
    report['sugestoes'] = suggestions(report)
    return report


def suggestions(r: dict) -> list[dict]:
    """Sugestões de organização a partir do retrato (cada uma pode virar pedido ao MESTRE)."""
    out = []
    add = lambda nivel, titulo, detalhe, pedido=None: out.append(
        {'nivel': nivel, 'titulo': titulo, 'detalhe': detalhe, 'pedido': pedido})
    od = r['downloads_antigos']
    if od['arquivos'] >= 20:
        add('alta', f"Downloads acumulados: {od['arquivos']} arquivos parados há mais de 90 dias ({human(od['tamanho'])})",
            'Arquivos baixados e esquecidos atrasam buscas e ocupam espaço. Separe o que vale guardar e arquive o resto.',
            f"Organize minha pasta {r['download_dir']}: liste os arquivos sem uso há mais de 90 dias, agrupe por tipo e "
            'proponha um destino para cada grupo (Documentos, Imagens, arquivo morto). Mova só depois da minha confirmação; não apague nada.')
    if r['instaladores']:
        tot = sum(i['tamanho'] for i in r['instaladores'])
        add('media', f"{len(r['instaladores'])} instaladores em Downloads ({human(tot)})",
            'Pacotes .deb, AppImage, ISO e .exe já instalados raramente são necessários de novo.',
            'Liste os instaladores (.deb, .AppImage, .iso, .exe) da minha pasta de Downloads, diga quais programas já estão '
            'instalados e proponha remover os desnecessários, pedindo minha confirmação.')
    if r['duplicados']:
        add('alta', f"{r['duplicados_total']} grupos de arquivos duplicados ({human(r['duplicados_espaco'])} recuperáveis)",
            'O mesmo arquivo em várias pastas gera dúvida sobre qual é a versão certa.',
            'Analise os arquivos duplicados das minhas pastas pessoais (mesmo conteúdo), diga qual cópia manter em cada '
            'grupo pela localização mais lógica e proponha mover as outras para a lixeira, com minha confirmação.')
    if r['copias_total'] >= 10:
        add('media', f"{r['copias_total']} arquivos com nome de cópia ou genérico",
            'Nomes como "(1)", "cópia", "final2" ou "Sem título" dificultam encontrar a versão definitiva.',
            'Encontre nas minhas pastas pessoais arquivos com nomes de cópia ou genéricos ("(1)", "cópia", "final", '
            '"Sem título") e sugira nomes descritivos no padrão AAAA-MM-DD_assunto. Renomeie só após minha confirmação.')
    if r['vazias_total'] >= 10:
        add('baixa', f"{r['vazias_total']} pastas vazias", 'Pastas vazias poluem a navegação.',
            'Liste as pastas vazias das minhas pastas pessoais e proponha remover as que não fazem parte de projetos, com confirmação.')
    if len(r['soltos_area_trabalho']) >= 8:
        add('media', f"Área de trabalho com {len(r['soltos_area_trabalho'])} itens soltos",
            'Área de trabalho cheia distrai. Mantenha só o que está em uso nesta semana.',
            'Organize minha Área de trabalho: proponha uma pasta de destino para cada item solto (exceto atalhos .desktop) e mova após minha confirmação.')
    if len(r['soltos_home']) >= 5:
        add('baixa', f"{len(r['soltos_home'])} arquivos soltos direto na pasta pessoal",
            'A raiz da pasta pessoal deve ter só pastas.',
            'Liste os arquivos soltos direto na minha pasta pessoal (não ocultos) e proponha para onde mover cada um, com confirmação.')
    big_old = [b for b in r['grandes'] if time.time() - b['mod'] > 180 * 86400]
    if big_old:
        add('media', f"{len(big_old)} arquivos grandes sem mudança há mais de 6 meses ({human(sum(b['tamanho'] for b in big_old))})",
            'Vídeos, imagens de disco e backups antigos podem ir para um HD externo ou para a nuvem.',
            'Liste meus arquivos com mais de 50 MB sem modificação há mais de 6 meses e sugira o que arquivar fora do disco.')
    docs = r['pastas'].get('Documentos') or next((v for k, v in r['pastas'].items() if 'ocument' in k), None)
    if docs and docs['arquivos'] > 300:
        add('baixa', 'Padrão de pastas para Documentos',
            'Um esquema fixo (ex.: 01-Projetos, 02-Áreas, 03-Referências, 04-Arquivo, o método PARA) torna o lugar de cada arquivo óbvio.',
            'Analise a estrutura de primeiro e segundo nível da minha pasta Documentos e proponha uma organização no método '
            'PARA (Projetos, Áreas, Recursos, Arquivo), com um mapa do que iria para onde. Não mova nada ainda.')
    add('baixa', 'Rotina semanal de 10 minutos',
        'Agendar uma triagem semanal (Downloads, Área de trabalho, duplicados) evita que a bagunça volte.',
        None)
    order = {'alta': 0, 'media': 1, 'baixa': 2}
    return sorted(out, key=lambda s: order[s['nivel']])


# ---------------------------------------------------------------- programas
APP_DIRS = [
    (Path('/usr/share/applications'), 'sistema'),
    (Path('/usr/local/share/applications'), 'sistema'),
    (HOME / '.local/share/applications', 'usuário'),
    (Path('/var/lib/flatpak/exports/share/applications'), 'flatpak'),
    (HOME / '.local/share/flatpak/exports/share/applications', 'flatpak'),
    (Path('/var/lib/snapd/desktop/applications'), 'snap'),
]
CAT_PT = {
    'AudioVideo': 'Áudio e vídeo', 'Audio': 'Áudio e vídeo', 'Video': 'Áudio e vídeo', 'Development': 'Desenvolvimento',
    'Education': 'Educação', 'Game': 'Jogos', 'Graphics': 'Gráficos', 'Network': 'Internet', 'Office': 'Escritório',
    'Science': 'Ciência', 'Settings': 'Configurações', 'System': 'Sistema', 'Utility': 'Utilitários',
}


def _loc(sec, key):
    for k in (f'{key}[pt_BR]', f'{key}[pt]', key):
        if sec.get(k):
            return sec.get(k)
    return ''


def programs() -> list[dict]:
    """Programas com atalho (o que aparece no menu de aplicativos)."""
    apps = {}
    for folder, origin in APP_DIRS:
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob('*.desktop')):
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            cp.optionxform = str
            try:
                cp.read(f, encoding='utf-8')
                sec = cp['Desktop Entry']
            except Exception:
                continue
            if sec.get('Type') != 'Application' or sec.get('NoDisplay', '').lower() == 'true' \
                    or sec.get('Hidden', '').lower() == 'true':
                continue
            cats = [c for c in sec.get('Categories', '').split(';') if c]
            main = next((CAT_PT[c] for c in cats if c in CAT_PT), 'Outros')
            apps[f.stem] = {'id': f.stem, 'nome': _loc(sec, 'Name'), 'generico': _loc(sec, 'GenericName'),
                            'descricao': _loc(sec, 'Comment'), 'categoria': main, 'categorias': cats,
                            'origem': origin, 'exec': re.sub(r'\s%\w', '', sec.get('Exec', ''))[:200],
                            'palavras': _loc(sec, 'Keywords')}
    return sorted(apps.values(), key=lambda a: a['nome'].lower())


def packages() -> list[dict]:
    """Pacotes apt instalados manualmente, com o resumo do próprio pacote."""
    try:
        manual = set(subprocess.run(['apt-mark', 'showmanual'], capture_output=True, text=True, timeout=30).stdout.split())
        rows = subprocess.run(['dpkg-query', '-W', '-f=${Package}\t${Version}\t${Section}\t${binary:Summary}\n'],
                              capture_output=True, text=True, timeout=60).stdout.splitlines()
    except Exception:
        return []
    out = []
    for row in rows:
        parts = row.split('\t')
        if len(parts) == 4 and parts[0] in manual and not parts[0].startswith(('lib', 'linux-', 'fonts-', 'language-')):
            out.append({'id': parts[0], 'nome': parts[0], 'versao': parts[1], 'secao': parts[2], 'descricao': parts[3]})
    return out


# ---------------------------------------------------------------- projetos
PROJECT_MARKERS = {'.git': 'git', 'package.json': 'Node', 'pyproject.toml': 'Python', 'requirements.txt': 'Python',
                   'docker-compose.yml': 'Docker', 'compose.yml': 'Docker', 'Cargo.toml': 'Rust', 'go.mod': 'Go',
                   '.obsidian': 'Obsidian'}


def _git(path, *args):
    try:
        return subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception:
        return ''


def projects(max_depth=5, seconds=25) -> list[dict]:
    """Pastas de projeto: repositórios git, apps Node/Python, composições Docker e cofres do Obsidian."""
    deadline = time.time() + seconds
    found = []
    stack = [(HOME, 0)]
    while stack and time.time() < deadline:
        d, depth = stack.pop()
        try:
            names = set(os.listdir(d))
        except OSError:
            continue
        kinds = [k for m, k in PROJECT_MARKERS.items() if m in names]
        if kinds and d != HOME:
            found.append((d, sorted(set(kinds))))
            continue  # não procura projetos dentro de projetos
        if depth >= max_depth:
            continue
        for n in names:
            if n.startswith('.') or n in SKIP_DIRS:
                continue
            p = d / n
            if p.is_dir() and not p.is_symlink():
                stack.append((p, depth + 1))
    out = []
    for path, kinds in found:
        item = {'caminho': str(path), 'nome': path.name, 'tipos': kinds, 'pai': str(path.parent).replace(str(HOME), '~')}
        try:
            item['mod'] = max((path / n).stat().st_mtime for n in os.listdir(path)[:200])
        except (OSError, ValueError):
            item['mod'] = 0
        if 'git' in kinds:
            item['branch'] = _git(path, 'rev-parse', '--abbrev-ref', 'HEAD')
            item['alteracoes'] = len([l for l in _git(path, 'status', '--porcelain').splitlines() if l.strip()])
            last = _git(path, 'log', '-1', '--format=%ct\t%s')
            if '\t' in last:
                ts, msg = last.split('\t', 1)
                item['ultimo_commit'] = {'quando': int(ts), 'msg': msg[:100]}
            item['remoto'] = bool(_git(path, 'remote'))
            ahead = _git(path, 'rev-list', '--count', '@{u}..HEAD')
            item['nao_enviados'] = int(ahead) if ahead.isdigit() else None
        out.append(item)
    out.sort(key=lambda p: -max(p.get('mod', 0), (p.get('ultimo_commit') or {}).get('quando', 0)))
    return out


# ---------------------------------------------------------------- fluxos
FLOW_TEMPLATES = [
    {'id': 'triagem-downloads', 'nome': 'Triagem semanal de Downloads', 'icone': '📥', 'repetir': 'semanal',
     'descricao': 'Classifica tudo que chegou em Downloads e propõe o destino de cada grupo; instaladores velhos vão para a lixeira com confirmação.',
     'pedido': 'Faça a triagem da minha pasta Downloads: agrupe por tipo e assunto, proponha destino (Documentos, Imagens, projetos '
               'existentes, arquivo morto) e liste instaladores e duplicados descartáveis. Mova ou apague só com minha confirmação.'},
    {'id': 'foco-do-dia', 'nome': 'Foco do dia', 'icone': '🎯', 'repetir': 'dias-uteis',
     'descricao': 'Cruza agenda do Google, e-mails importantes, lembretes do MESTRE e projetos ativos e monta um plano com 3 prioridades.',
     'pedido': 'Monte meu foco do dia: veja meus compromissos de hoje no Google Agenda, os e-mails importantes não lidos do Gmail, '
               'minha agenda do MESTRE e os projetos com alterações recentes. Responda com 3 prioridades, os horários livres para '
               'trabalho focado e o que posso adiar. Salve o plano na memória do Obsidian.'},
    {'id': 'status-projetos', 'nome': 'Painel de projetos', 'icone': '🧭', 'repetir': 'diario',
     'descricao': 'Verifica todos os repositórios: alterações sem commit, commits não enviados, projetos parados há semanas.',
     'pedido': 'Verifique o estado de todos os meus projetos git: alterações não commitadas, commits não enviados, branch atual e '
               'há quanto tempo cada um está parado. Aponte riscos (trabalho sem backup) e sugira o próximo passo de cada projeto ativo.'},
    {'id': 'backup-projetos', 'nome': 'Proteção de projetos', 'icone': '🛡', 'repetir': 'semanal',
     'descricao': 'Encontra projetos sem repositório remoto ou com trabalho não enviado e prepara o backup (sem enviar repositórios públicos sem você conferir).',
     'pedido': 'Encontre meus projetos sem repositório remoto ou com commits não enviados. Para cada um, diga o risco e proponha o '
               'backup adequado. Nunca faça push sem minha confirmação e confira se não há segredos nos arquivos antes.'},
    {'id': 'revisao-semanal', 'nome': 'Revisão semanal (GTD)', 'icone': '🗓', 'repetir': 'semanal',
     'descricao': 'Resumo da semana (commits, arquivos novos, e-mails pendentes, compromissos da próxima semana) gravado como nota no Obsidian.',
     'pedido': 'Faça minha revisão semanal: o que avancei nos projetos (commits da semana), arquivos novos importantes, e-mails que '
               'aguardam resposta, compromissos da próxima semana e pendências da agenda do MESTRE. Grave como nota datada na memória do Obsidian e me diga os 3 pontos principais.'},
    {'id': 'notas-projetos', 'nome': 'Projetos ↔ Segundo Cérebro', 'icone': '🧠', 'repetir': 'semanal',
     'descricao': 'Cria ou atualiza no Obsidian uma nota por projeto ativo: objetivo, estado, últimos commits e próximos passos.',
     'pedido': 'Para cada projeto meu com atividade nas últimas 4 semanas, crie ou atualize uma nota na memória do Obsidian com '
               'objetivo (leia o README), estado atual, últimos commits e próximos passos, ligando as notas entre si.'},
    {'id': 'limpeza-disco', 'nome': 'Espaço em disco', 'icone': '🧹', 'repetir': 'semanal',
     'descricao': 'Duplicados, arquivos grandes esquecidos, caches e imagens Docker sem uso: relatório do espaço recuperável.',
     'pedido': 'Analise o uso do disco: duplicados nas pastas pessoais, arquivos grandes esquecidos, caches grandes e imagens/volumes '
               'Docker sem uso. Diga quanto dá para recuperar em cada item e execute só o que eu confirmar.'},
    {'id': 'inbox-documentos', 'nome': 'Documentos novos → resumo', 'icone': '📄', 'repetir': 'diario',
     'descricao': 'Lê os PDFs e documentos que chegaram nas últimas 24 h, resume cada um e sugere a pasta certa.',
     'pedido': 'Veja os documentos (PDF, DOCX, planilhas) criados ou baixados nas últimas 24 horas nas minhas pastas pessoais, '
               'faça um resumo de uma linha de cada e sugira a pasta adequada. Mova só com minha confirmação.'},
]
