"""Notícias do MESTRE a partir do NewsFlash (leitor de RSS).

Usa as assinaturas e categorias que o usuário mantém no NewsFlash (banco
SQLite, aberto só para leitura) e busca as manchetes ao vivo nesses feeds,
para não depender de o aplicativo ter sincronizado. Sem internet, usa os
artigos já guardados pelo NewsFlash.
"""
import html
import json
import os
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

APP_ID = 'io.gitlab.news_flash.NewsFlash'
DB_CANDIDATES = [
    Path.home() / '.var/app' / APP_ID / 'data/news-flash/database.sqlite',  # Flatpak
    Path.home() / '.local/share/news-flash/database.sqlite',  # instalação nativa
]
CACHE = Path('/tmp') / f'mestre-noticias-{os.getuid()}.json'
CACHE_TTL = 600
UA = 'Mozilla/5.0 (X11; Linux x86_64) MESTRE-noticias/1.0'


def db_path():
    return next((p for p in DB_CANDIDATES if p.exists()), None)


def _connect():
    path = db_path()
    if not path:
        raise RuntimeError('NewsFlash não encontrado (nenhum banco em ~/.var/app ou ~/.local/share).')
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)


def feeds():
    """Assinaturas do NewsFlash: [{id, nome, site, url, categoria, nao_lidas}]."""
    with _connect() as c:
        rows = c.execute('''
            SELECT f.feed_id, f.label, f.website, f.feed_url, COALESCE(cat.label, ''),
                   (SELECT COUNT(*) FROM articles a WHERE a.feed_id = f.feed_id AND a.unread = 1)
            FROM feeds f
            LEFT JOIN feed_mapping m ON m.feed_id = f.feed_id
            LEFT JOIN categories cat ON cat.category_id = m.category_id
            ORDER BY cat.label, f.label''').fetchall()
    return [{'id': r[0], 'nome': r[1], 'site': r[2], 'url': r[3] or r[0], 'categoria': r[4], 'nao_lidas': r[5]}
            for r in rows]


def _clean(text, limit=None):
    t = html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))
    t = re.sub(r'\s+', ' ', t).strip()
    # rodapé que os sites WordPress colam no resumo
    t = re.sub(r'\s*(The post|O post)\s.*?(appeared first on|apareceu primeiro em)\s.*$', '', t).strip()
    return (t[: limit - 1] + '…') if limit and len(t) > limit else t


def _date(text):
    if not text:
        return 0.0
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.strip().replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def _fetch_feed(feed):
    """Baixa e lê um feed RSS 2.0 ou Atom."""
    req = urllib.request.Request(feed['url'], headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=10) as r:
        root = ET.fromstring(r.read())
    items = []
    atom = '{http://www.w3.org/2005/Atom}'
    for it in root.iter('item'):
        items.append({'titulo': _clean(it.findtext('title')), 'link': (it.findtext('link') or '').strip(),
                      'resumo': _clean(it.findtext('description'), 280), 'data': _date(it.findtext('pubDate'))})
    for it in root.iter(f'{atom}entry'):
        link = it.find(f'{atom}link')
        items.append({'titulo': _clean(it.findtext(f'{atom}title')),
                      'link': link.get('href') if link is not None else '',
                      'resumo': _clean(it.findtext(f'{atom}summary') or it.findtext(f'{atom}content'), 280),
                      'data': _date(it.findtext(f'{atom}updated') or it.findtext(f'{atom}published'))})
    for i in items:
        i.update(fonte=feed['nome'], categoria=feed['categoria'])
    return items


def _from_db(limit=200):
    with _connect() as c:
        rows = c.execute('''
            SELECT a.title, a.url, a.summary, a.date, f.label, COALESCE(cat.label, ''), a.unread
            FROM articles a JOIN feeds f ON f.feed_id = a.feed_id
            LEFT JOIN feed_mapping m ON m.feed_id = f.feed_id
            LEFT JOIN categories cat ON cat.category_id = m.category_id
            ORDER BY a.date DESC LIMIT ?''', (limit,)).fetchall()
    return [{'titulo': _clean(r[0]), 'link': r[1] or '', 'resumo': _clean(r[2], 280), 'data': _date(r[3]),
             'fonte': r[4], 'categoria': r[5], 'nao_lida': bool(r[6])} for r in rows]


def headlines(categoria=None, fonte=None, n=15, refresh=False):
    """Manchetes mais recentes dos feeds do NewsFlash (ao vivo, com cache de 10 min)."""
    items, origem = None, 'ao vivo'
    if not refresh and CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL:
        try:
            items = json.loads(CACHE.read_text())
        except ValueError:
            items = None
    if items is None:
        fs = feeds()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda f: _safe(_fetch_feed, f), fs))
        items = [i for r in results if r for i in r]
        if items:
            CACHE.write_text(json.dumps(items, ensure_ascii=False))
        else:
            items, origem = _from_db(), 'guardadas no NewsFlash (sem internet)'
    seen = set()
    out = []
    for i in sorted(items, key=lambda x: x['data'], reverse=True):
        if not i['titulo'] or i['titulo'] in seen:
            continue
        if categoria and categoria.lower() not in (i.get('categoria') or '').lower():
            continue
        if fonte and fonte.lower() not in (i.get('fonte') or '').lower():
            continue
        seen.add(i['titulo'])
        out.append(i)
        if len(out) >= n:
            break
    return out, origem


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def search(term, n=10):
    """Busca nas notícias guardadas (texto completo) e nas manchetes ao vivo."""
    found = []
    try:
        with _connect() as c:
            rows = c.execute('''
                SELECT a.title, a.url, a.summary, a.date, f.label FROM articles a
                JOIN feeds f ON f.feed_id = a.feed_id
                WHERE a.title LIKE ? OR a.summary LIKE ? ORDER BY a.date DESC LIMIT ?''',
                             (f'%{term}%', f'%{term}%', n)).fetchall()
        found += [{'titulo': _clean(r[0]), 'link': r[1], 'resumo': _clean(r[2], 280), 'data': _date(r[3]),
                   'fonte': r[4]} for r in rows]
    except Exception:
        pass
    live, _ = headlines(n=200)
    t = term.lower()
    found += [i for i in live if t in i['titulo'].lower() or t in (i.get('resumo') or '').lower()]
    seen, out = set(), []
    for i in sorted(found, key=lambda x: x['data'], reverse=True):
        if i['titulo'] not in seen:
            seen.add(i['titulo'])
            out.append(i)
    return out[:n]


def read_article(url):
    """Texto de um artigo: do NewsFlash, se ele já baixou; senão, da página."""
    try:
        with _connect() as c:
            r = c.execute('''SELECT COALESCE(NULLIF(c.scraped_content, ''), NULLIF(c.plain_text, ''), c.html)
                             FROM articles a JOIN article_contents c ON c.article_id = a.article_id
                             WHERE a.url = ?''', (url,)).fetchone()
        if r and r[0]:
            return _clean(r[0], 6000)
    except Exception:
        pass
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        page = resp.read().decode('utf-8', 'replace')
    page = re.sub(r'(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>', ' ', page)
    paragraphs = [_clean(p) for p in re.findall(r'(?is)<p[^>]*>(.*?)</p>', page)]
    return ' '.join(p for p in paragraphs if len(p) > 60)[:6000] or 'Não consegui extrair o texto da página.'


def when(ts):
    if not ts:
        return ''
    d = datetime.fromtimestamp(ts, timezone.utc).astimezone()
    delta = time.time() - ts
    if delta < 3600:
        return f'há {max(1, int(delta // 60))} min'
    if delta < 86400:
        return f'há {int(delta // 3600)} h'
    return d.strftime('%d/%m %H:%M')
