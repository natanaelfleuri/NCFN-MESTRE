"""Ponte do MESTRE com o portal NCFN (uso online).

O portal abre uma janela suspensa que conversa com o MESTRE por WebSocket em
/portal/ws. Pela internet, a conexão chega pelo Caddy do portal, que fica
numa rede Docker: por isso o MESTRE abre um segundo ouvinte só no gateway
dessa rede (nunca em 0.0.0.0), que atende apenas /portal/* e exige o token do
Cloudflare Access (assinatura RS256, AUD da aplicação e e-mail autorizado).

As ações na página (ler, navegar, clicar, preencher, chamar a API) são
executadas pela própria janela, no navegador do usuário, com a sessão dele:
o cérebro do portal só tem o comando `ncfn-tela`, que repassa o pedido para
a janela e devolve o resultado.
"""
import asyncio
import contextlib
import subprocess
import time
import uuid

import jwt
import uvicorn

_jwks: dict[str, jwt.PyJWKClient] = {}


def settings(config: dict) -> dict:
    p = config.get('portal') or {}
    return {'enabled': bool(p.get('enabled')), 'bind': p.get('bind', 'auto'),
            'docker_network': p.get('docker_network', ''), 'team_domain': (p.get('cf_team_domain') or '').rstrip('/'),
            'aud': [a for a in p.get('cf_aud', []) if a], 'emails': [e.lower() for e in p.get('allowed_emails', [])],
            'origins': [o.rstrip('/') for o in p.get('origins', [])],
            'keep_alive': p.get('keep_alive', True), 'voice': p.get('voice', False)}


def verify_access(token: str, cfg: dict) -> str | None:
    """E-mail do token do Cloudflare Access, se ele for válido para o portal."""
    if not token or not cfg['team_domain'] or not cfg['aud']:
        return None  # sem configuração completa, falha fechado
    try:
        client = _jwks.get(cfg['team_domain'])
        if client is None:
            client = _jwks[cfg['team_domain']] = jwt.PyJWKClient(
                f"{cfg['team_domain']}/cdn-cgi/access/certs", cache_keys=True, lifespan=600)
        key = client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=['RS256'], audience=cfg['aud'], issuer=cfg['team_domain'])
    except Exception as e:
        print('[portal] token recusado:', type(e).__name__, flush=True)
        return None
    email = str(claims.get('email') or '').lower()
    return email if email and email in cfg['emails'] else None


def bridge_address(cfg: dict) -> str | None:
    """IP do gateway da rede Docker do portal (onde o Caddy alcança o host)."""
    if cfg['bind'] not in ('', 'auto'):
        return cfg['bind']
    if not cfg['docker_network']:
        return None
    try:
        out = subprocess.run(['docker', 'network', 'inspect', cfg['docker_network'], '-f',
                              '{{range .IPAM.Config}}{{.Gateway}} {{end}}'],
                             capture_output=True, text=True, timeout=10).stdout.split()
    except Exception:
        return None
    return next((ip for ip in out if ip.count('.') == 3), None)


def only_portal(app):
    """ASGI do ouvinte da rede Docker: só /portal/*, marcado como remoto."""
    async def wrapped(scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            if not scope.get('path', '').startswith('/portal/'):
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    await send({'type': 'http.response.start', 'status': 404,
                                'headers': [(b'content-type', b'text/plain')]})
                    await send({'type': 'http.response.body', 'body': b'not found'})
                return
            scope = {**scope, 'mestre_remote': True}
        await app(scope, receive, send)
    return wrapped


class _Server(uvicorn.Server):
    # O servidor principal já cuida dos sinais do processo.
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve_bridge(app, cfg: dict, port: int):
    """Segundo ouvinte, no gateway da rede Docker. Tenta de novo se o Docker ainda não subiu."""
    while True:
        host = bridge_address(cfg)
        if host:
            server = _Server(uvicorn.Config(only_portal(app), host=host, port=port, lifespan='off',
                                            log_level='warning', proxy_headers=False))
            print(f'[portal] ponte em {host}:{port}', flush=True)
            try:
                await server.serve()
            except (OSError, SystemExit) as e:
                print('[portal] ponte falhou:', e, flush=True)
            if server.should_exit and server.started:
                return
        await asyncio.sleep(30)


class Relay:
    """Leva comandos do `ncfn-tela` até a janela do portal e traz a resposta."""

    def __init__(self):
        self.clients = {}  # ws -> {'email', 'page', 'active', 'voice'}
        self.waiting: dict[str, asyncio.Future] = {}

    def active(self):
        live = sorted(self.clients.items(), key=lambda kv: kv[1]['active'], reverse=True)
        return live[0] if live else (None, None)

    async def run(self, cmd: str, args: list, timeout=150):
        ws, info = self.active()
        if ws is None:
            return {'ok': False, 'erro': 'Nenhuma janela do portal NCFN aberta.'}
        cid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        self.waiting[cid] = fut
        try:
            await ws.send_json({'type': 'cmd', 'id': cid, 'cmd': cmd, 'args': args})
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return {'ok': False, 'erro': 'A janela do portal não respondeu a tempo.'}
        except Exception as e:
            return {'ok': False, 'erro': f'Falha ao falar com a janela: {e}'}
        finally:
            self.waiting.pop(cid, None)

    def answer(self, data: dict):
        fut = self.waiting.get(data.get('id'))
        if fut and not fut.done():
            fut.set_result({'ok': bool(data.get('ok')), **({'resultado': data.get('result')} if data.get('ok')
                                                           else {'erro': data.get('error') or 'falhou'})})

    def touch(self, ws, **fields):
        if ws in self.clients:
            self.clients[ws].update(active=time.time(), **fields)
