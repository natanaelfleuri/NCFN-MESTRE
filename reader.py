"""Leitor do MESTRE: biblioteca de documentos e leitura em voz alta.

Documentos enviados (PDF, EPUB, DOCX, TXT, MD, HTML) ficam em
~/Documentos/MESTRE/biblioteca. O texto é extraído uma vez para um .txt ao
lado do original, para a IA ler e responder perguntas, e é dividido em
trechos curtos (com a página de origem) para a leitura em voz alta, que
guarda a posição de cada documento.
"""
import html
import json
import re
import time
import uuid
import zipfile
from pathlib import Path

HOME = Path.home()
DOCS = (HOME / 'Documentos' if (HOME / 'Documentos').is_dir() else HOME / 'Documents') / 'MESTRE'
LIBRARY = DOCS / 'biblioteca'
INDEX = Path(__file__).resolve().parent / 'state' / 'library.json'
SUPPORTED = ('.pdf', '.epub', '.docx', '.txt', '.md', '.html', '.htm')
CHUNK = 320  # caracteres por trecho falado


def _strip_html(text):
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', text)
    text = re.sub(r'(?i)<br\s*/?>|</p>|</h\d>|</li>|</div>', '\n', text)
    return html.unescape(re.sub(r'<[^>]+>', ' ', text))


def extract(path: Path) -> list[tuple[int, str]]:
    """Lista de (página ou seção, texto)."""
    ext = path.suffix.lower()
    if ext == '.pdf':
        from pypdf import PdfReader
        pages = []
        for i, page in enumerate(PdfReader(str(path)).pages, 1):
            try:
                pages.append((i, page.extract_text() or ''))
            except Exception:
                pages.append((i, ''))
        return pages
    if ext == '.epub':
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(('.xhtml', '.html', '.htm'))]
            # ordem de leitura do spine, quando existir
            opf = next((n for n in z.namelist() if n.endswith('.opf')), None)
            if opf:
                o = z.read(opf).decode('utf-8', 'replace')
                hrefs = dict(re.findall(r'<item[^>]+id="([^"]+)"[^>]+href="([^"]+)"', o))
                base = opf.rsplit('/', 1)[0] + '/' if '/' in opf else ''
                spine = [base + hrefs[i] for i in re.findall(r'<itemref[^>]+idref="([^"]+)"', o) if i in hrefs]
                names = [n for n in spine if n in z.namelist()] or names
            return [(i, _strip_html(z.read(n).decode('utf-8', 'replace'))) for i, n in enumerate(names, 1)]
    if ext == '.docx':
        with zipfile.ZipFile(path) as z:
            xml = z.read('word/document.xml').decode('utf-8', 'replace')
        xml = re.sub(r'</w:p>', '\n', xml)
        return [(1, html.unescape(re.sub(r'<[^>]+>', '', xml)))]
    raw = path.read_text('utf-8', 'replace')
    if ext in ('.html', '.htm'):
        raw = _strip_html(raw)
    if ext == '.md':
        raw = re.sub(r'[#*_`>]+', ' ', raw)
    return [(1, raw)]


def chunk(pages: list[tuple[int, str]]) -> list[dict]:
    """Trechos falados: parágrafos quebrados em frases, juntados até ~CHUNK caracteres."""
    out = []
    for page, text in pages:
        # PDFs quebram linhas no meio da frase: junta linhas, mantém parágrafos
        text = re.sub(r'-\n(\w)', r'\1', text)
        paras = [re.sub(r'\s+', ' ', p).strip() for p in re.split(r'\n\s*\n|(?<=[.!?:])\s*\n', text)]
        for para in paras:
            if len(para) < 2:
                continue
            sentences = re.split(r'(?<=[.!?…])\s+', para)
            buf = ''
            for s in sentences:
                if buf and len(buf) + len(s) > CHUNK:
                    out.append({'p': page, 't': buf.strip()})
                    buf = ''
                while len(s) > CHUNK * 1.6:  # frase enorme (tabela, lista sem pontuação)
                    cut = s.rfind(' ', 0, CHUNK) or CHUNK
                    out.append({'p': page, 't': s[:cut].strip()})
                    s = s[cut:]
                buf += ' ' + s
            if buf.strip():
                out.append({'p': page, 't': buf.strip(), 'end': True})  # fim de parágrafo
    return out


def _load():
    try:
        return json.loads(INDEX.read_text())
    except (OSError, ValueError):
        return []


def _save(items):
    INDEX.parent.mkdir(exist_ok=True)
    INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1))


def documents():
    return _load()


def get(doc_id):
    return next((d for d in _load() if d['id'] == doc_id), None)


def chunks_of(doc):
    return json.loads(Path(doc['chunks']).read_text())


def add(filename: str, data: bytes) -> dict:
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f'Formato não suportado: {ext or "sem extensão"}. Use PDF, EPUB, DOCX, TXT, MD ou HTML.')
    LIBRARY.mkdir(parents=True, exist_ok=True)
    doc_id = uuid.uuid4().hex[:8]
    dest = LIBRARY / name
    if dest.exists():
        dest = LIBRARY / f'{dest.stem}-{doc_id}{ext}'
    dest.write_bytes(data)
    pages = extract(dest)
    text_path = dest.with_suffix(dest.suffix + '.txt')
    text_path.write_text('\n\n'.join(f'[página {p}]\n{t}' for p, t in pages))
    parts = chunk(pages)
    chunks_path = INDEX.parent / f'library-{doc_id}.json'
    chunks_path.write_text(json.dumps(parts, ensure_ascii=False))
    doc = {'id': doc_id, 'name': name, 'path': str(dest), 'text': str(text_path), 'chunks': str(chunks_path),
           'pages': max((p for p, _ in pages), default=0), 'total': len(parts), 'pos': 0,
           'chars': sum(len(t) for _, t in pages), 'added': time.time()}
    if not parts:
        doc['warning'] = 'Nenhum texto encontrado (PDF escaneado como imagem?).'
    items = _load()
    items.insert(0, doc)
    _save(items)
    return doc


def set_pos(doc_id, pos):
    items = _load()
    for d in items:
        if d['id'] == doc_id:
            d['pos'] = max(0, min(int(pos), d['total']))
            d['last'] = time.time()
    _save(items)


def remove(doc_id):
    items = _load()
    doc = next((d for d in items if d['id'] == doc_id), None)
    if doc:
        for key in ('path', 'text', 'chunks'):
            Path(doc[key]).unlink(missing_ok=True)
        _save([d for d in items if d['id'] != doc_id])
    return bool(doc)


def index_of_page(doc, page):
    for i, c in enumerate(chunks_of(doc)):
        if c['p'] >= page:
            return i
    return 0
