#!/usr/bin/env python3
"""LAN HTTPS console for the read-only preview worker."""
import argparse
import contextvars
import sites
import datetime as dt
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import ssl
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit, parse_qs
from preview import atomic
import deploy
import smartdns_manager
from lookup import lookup
LOOKUP_SLOTS = threading.BoundedSemaphore(2)

BASE = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get('APP_PRIVATE', str(Path.home() / '.config/meraki-split-preview')))
SESSIONS = {}
ATTEMPTS = {}
LOCK = threading.Lock()
REQUEST_SITE = contextvars.ContextVar("request_site", default="default")

def config():
    return sites.get(REQUEST_SITE.get())


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default if default is not None else {}


def command(*args):
    if os.environ.get('CONTAINER_RUNTIME') == '1' or (REQUEST_SITE.get() != 'default' and not any('smartdns' in arg for arg in args)):
        from runtime import jobs
        return jobs.command(*args, site=REQUEST_SITE.get())
    return subprocess.run(['systemctl', '--user', *args], capture_output=True,
                          text=True, timeout=10)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()


def init_auth():
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = PRIVATE / 'gui-auth.json'
    if not path.exists():
        password, salt = secrets.token_urlsafe(18), secrets.token_hex(16)
        atomic(path, json.dumps({'username': 'admin', 'salt': salt,
                                'hash': password_hash(password, salt)}))
        atomic(PRIVATE / 'gui-initial-password', password + '\n')


def latest_snapshot(root, latest):
    if latest.get('meraki_status') != 'read_success':
        return None
    try:
        stamp = dt.datetime.fromisoformat(latest['generated_at_utc']).astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        snapshot = read(root / 'runs' / stamp / 'meraki-api-snapshot.json', None)
        if snapshot.get('networkId') != latest.get('network_id'):
            return None
        return snapshot
    except (ValueError, KeyError):
        return None


def state():
    cfg = config()
    root = Path(cfg['state_dir']).expanduser()
    latest = read(root / 'latest.json')
    status = read(root / 'status.json')
    history = []
    for folder in sorted((root / 'runs').glob('*'), reverse=True):
        r = read(folder / 'report.json')
        if r:
            history.append({k: r.get(k) for k in ('generated_at_utc', 'china_prefix_count', 'meraki_status')})
        if len(history) == 20:
            break
    props = command('show', 'meraki-split-preview.service', '-p', 'ActiveState', '-p', 'SubState').stdout
    timer = command('show', 'meraki-split-preview.timer', '-p', 'NextElapseUSecRealtime', '-p', 'ActiveState').stdout
    key = Path(cfg['api_key_file']).expanduser()
    return {'site_id': REQUEST_SITE.get(), 'sites': sites.listing(), 'latest': latest, 'status': status, 'history': history,
            'running': 'ActiveState=activating' in props or 'ActiveState=active\n' in props,
            'timer': timer, 'api_key_configured': key.exists() and key.stat().st_size > 0,
            'organization_id': cfg['organization_id'], 'network_name': cfg['network_name'],
            'meraki_snapshot': latest_snapshot(root, latest),
            'backup': read(root / 'backups' / 'latest.json'),
            'backup_status': read(root / 'backups' / 'status.json'),
            'deployment_status': read(root / 'deployment' / 'status.json'),
            'deployment_plan': read(root / 'deployment' / 'latest-plan.json'),
            'deployment_plan_status': read(root / 'deployment' / 'plan-status.json'),
            'deployment_last_success': read(root / 'deployment' / 'last-success.json'),
            'deployment_settings': read(root / 'deployment' / 'settings.json'),
            'managed_count': len(read(root / 'deployment' / 'managed.json').get('rules', [])),
            'smartdns': smartdns_manager.state()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Never log request bodies, cookies or credentials.
        pass

    def reply(self, code, obj, content_type='application/json; charset=utf-8', cookie=None):
        data = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get('Host', '') in self.server.allowed_hosts

    def session(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie['session'].value
        except (KeyError, ValueError):
            return None, None
        with LOCK:
            entry = SESSIONS.get(token)
            if entry and entry['expires'] > time.time():
                return token, entry
            SESSIONS.pop(token, None)
        return None, None

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {'error': 'Invalid host'})
        path = urlsplit(self.path).path
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path in assets:
            name, mime = assets[path]
            return self.reply(200, (BASE / 'web' / name).read_bytes(), mime)
        _, session = self.session()
        if not session:
            return self.reply(401, {'error': '请先登录'})
        try:
            site = self.headers.get('X-Site-ID') or parse_qs(urlsplit(self.path).query).get('site', ['default'])[0]
            sites.get(site)
            REQUEST_SITE.set(site)
        except ValueError as exc:
            return self.reply(400, {'error': str(exc)})
        if path == '/api/state':
            try:
                obj = state()
                obj['csrf'] = session['csrf']
                return self.reply(200, obj)
            except Exception:
                return self.reply(500, {'error': '状态读取失败，请检查服务日志与配置'})
        if path == '/api/dns-domains':
            try:
                q = parse_qs(urlsplit(self.path).query)
                return self.reply(200, smartdns_manager.search(q.get('q', [''])[0], int(q.get('page', ['0'])[0])))
            except ValueError as exc:
                return self.reply(400, {'error': str(exc)})
        if path == '/api/dns-domains.txt':
            file = smartdns_manager.ROOT / 'current/china-domains.txt'
            return self.reply(200, file.read_bytes(), 'text/plain; charset=utf-8') if file.exists() else self.reply(404, {'error': '尚无域名列表'})
        if path == '/api/backup.tar.gz':
            cfg = config()
            root = Path(cfg['state_dir']).expanduser() / 'backups'
            manifest = read(root / 'latest.json')
            name = manifest.get('id', '')
            if not name or any(c not in '0123456789T.Z' for c in name):
                return self.reply(404, {'error': '尚无备份'})
            archive = root / (name + '.tar.gz')
            if not archive.exists():
                return self.reply(404, {'error': '备份归档尚未就绪'})
            return self.reply(200, archive.read_bytes(), 'application/gzip')
        if path == '/api/restore-guide':
            return self.reply(200, (BASE / 'RESTORE.md').read_bytes(), 'text/plain; charset=utf-8')
        if path == '/api/meraki-snapshot.json':
            cfg = config()
            root = Path(cfg['state_dir']).expanduser()
            snapshot = latest_snapshot(root, read(root / 'latest.json'))
            return self.reply(200, snapshot) if snapshot is not None else self.reply(404, {'error': '尚无可用 Meraki 快照'})
        if path == '/api/prefixes.txt':
            cfg = config()
            latest = read(Path(cfg['state_dir']).expanduser() / 'latest.json')
            return self.reply(200, ('\n'.join(latest.get('china_prefixes', [])) + '\n').encode(), 'text/plain; charset=utf-8')
        return self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        host = self.headers.get('Host', '')
        scheme = 'https' if self.server.secure else 'http'
        if not self.valid_host() or self.headers.get('Origin') != scheme + '://' + host:
            return self.reply(403, {'error': '请求来源不匹配'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'JSON required'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= (131072 if urlsplit(self.path).path == '/api/dns-custom' else 8192):
                raise ValueError()
            obj = json.loads(self.rfile.read(size))
            if not isinstance(obj, dict):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            return self.reply(400, {'error': '请求格式错误'})
        path = urlsplit(self.path).path
        if path == '/api/login':
            ip, now = self.client_address[0], time.time()
            with LOCK:
                recent = [t for t in ATTEMPTS.get(ip, []) if now - t < 300]
                if len(recent) >= 10:
                    return self.reply(429, {'error': '尝试过于频繁，请五分钟后重试'})
                ATTEMPTS[ip] = recent + [now]
            auth = read(PRIVATE / 'gui-auth.json')
            password = obj.get('password', '')
            if not isinstance(password, str) or len(password) > 1024:
                return self.reply(400, {'error': '登录信息格式错误'})
            valid = hmac.compare_digest(password_hash(password, auth['salt']), auth['hash'])
            if obj.get('username') != auth['username'] or not valid:
                return self.reply(401, {'error': '账号或密码错误'})
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with LOCK:
                if read(PRIVATE/'gui-auth.json').get('hash') != auth['hash']:
                    return self.reply(401, {'error':'密码已更新，请重新登录'})
                for k in list(SESSIONS):
                    if SESSIONS[k]['expires'] <= now:
                        del SESSIONS[k]
                SESSIONS[token] = {'expires': now + 28800, 'csrf': csrf}
                ATTEMPTS.pop(ip, None)
            return self.reply(200, {'ok': True}, cookie='session=' + token +
                              '; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800' + ('; Secure' if self.server.secure else ''))
        token, session = self.session()
        if not session:
            return self.reply(401, {'error': '请先登录'})
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
            return self.reply(403, {'error': '安全校验失败，请重新登录'})
        if path == '/api/password':
            old = obj.get('current_password', '')
            new = obj.get('new_password', '')
            if not isinstance(old, str) or len(old)>1024 or not isinstance(new, str) or not 12<=len(new)<=128 or new!=obj.get('confirm_password'):
                return self.reply(400, {'error': '新密码须为12～128个字符，且两次输入一致'})
            with LOCK:
                ratekey='password:'+self.client_address[0]
                now=time.time();recent=[t for t in ATTEMPTS.get(ratekey,[]) if now-t<300]
                if len(recent)>=5:return self.reply(429, {'error':'改密尝试过多，请五分钟后重试'})
                ATTEMPTS[ratekey]=recent+[now]
                auth=read(PRIVATE/'gui-auth.json')
                if not hmac.compare_digest(password_hash(old,auth['salt']),auth['hash']):
                    return self.reply(400, {'error':'当前密码不正确'})
                if old==new:return self.reply(400, {'error':'新密码不能与当前密码相同'})
                salt=secrets.token_hex(16)
                atomic(PRIVATE/'gui-auth.json',json.dumps({'username':auth['username'],'salt':salt,'hash':password_hash(new,salt)}))
                (PRIVATE/'gui-initial-password').unlink(missing_ok=True)
                SESSIONS.clear();ATTEMPTS.pop(ratekey,None)
            return self.reply(200, {'message':'密码已更新，所有会话已退出，请用新密码登录'}, cookie='session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
        if path == '/api/sites':
            try:
                site=sites.create(obj)
                return self.reply(201, {'id':site,'message':'站点已添加；请先刷新并核对目标，再创建下发计划'})
            except ValueError as exc:
                return self.reply(400, {'error':str(exc)})
        try:
            site=self.headers.get('X-Site-ID') or parse_qs(urlsplit(self.path).query).get('site',['default'])[0]
            sites.get(site);REQUEST_SITE.set(site)
        except ValueError as exc:
            return self.reply(400, {'error':str(exc)})
        if path == '/api/logout':
            with LOCK:
                SESSIONS.pop(token, None)
            return self.reply(200, {'ok': True}, cookie='session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')
        if path in ('/api/dns-update', '/api/dns-custom'):
            props = command('show', 'unity-smartdns-update.service', '-p', 'ActiveState').stdout
            if 'ActiveState=activating' in props or 'ActiveState=active\n' in props:
                return self.reply(409, {'error': '域名更新正在进行，请稍后重试'})
            if path == '/api/dns-custom':
                try:
                    domains = smartdns_manager.custom_domains(obj.get('domains', ''))
                    smartdns_manager.ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
                    atomic(smartdns_manager.ROOT / 'custom-pending.json', json.dumps({'domains': domains, 'created_at': time.time()}))
                except ValueError as exc:
                    return self.reply(400, {'error': str(exc)})
            result = command('start', '--no-block', 'unity-smartdns-update.service')
            if result.returncode:
                return self.reply(500, {'error': '域名更新服务启动失败'})
            return self.reply(202, {'message': '域名更新已启动，验证成功后加载到 SmartDNS；不会修改 Meraki'})
        if path in ('/api/deploy-plan', '/api/deploy-apply', '/api/deploy-auto'):
            cfg = config()
            try:
                if path == '/api/deploy-plan':
                    return self.reply(200, deploy.plan(cfg))
                if path == '/api/deploy-auto':
                    enabled = obj.get('enabled')
                    if not isinstance(enabled, bool):
                        raise ValueError('无效开关值')
                    if enabled and not (read(deploy.paths(cfg) / 'last-china-success.json') or read(deploy.paths(cfg) / 'last-success.json').get('label') == 'china'):
                        raise ValueError('完整国内库尚未成功下发验证，不能启用自动下发')
                    atomic(deploy.paths(cfg) / 'settings.json', json.dumps({'automatic': enabled}))
                    return self.reply(200, {'message': '自动增量下发已' + ('启用' if enabled else '停用')})
                deploy.queue(cfg, obj.get('id', ''))
                result = command('start', '--no-block', 'meraki-split-deploy.service')
                if result.returncode:
                    (deploy.paths(cfg) / 'queued.json').unlink(missing_ok=True)
                    raise ValueError('下发任务启动失败')
                return self.reply(202, {'message': '已开始备份并合并下发，完成后自动读回核对'})
            except FileExistsError:
                return self.reply(409, {'error': '已有下发任务，请等待完成'})
            except Exception as exc:
                message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                return self.reply(400, {'error': message})
        if path == '/api/backup':
            result = command('start', '--no-block', 'meraki-config-backup.service')
            if result.returncode:
                return self.reply(500, {'error': '备份任务启动失败'})
            return self.reply(202, {'message': '已启动整体配置读取备份；过程中不修改 Meraki。'})
        if path == '/api/lookup':
            if not LOOKUP_SLOTS.acquire(blocking=False):
                return self.reply(429, {'error': '查询繁忙，请稍后重试'})
            try:
                return self.reply(200, lookup(obj.get('query', '')))
            except ValueError as exc:
                return self.reply(400, {'error': str(exc)})
            except Exception:
                return self.reply(500, {'error': '查询暂时失败，请稍后重试'})
            finally:
                LOOKUP_SLOTS.release()
        if path == '/api/key':
            key = obj.get('key', '')
            if not isinstance(key, str) or not 20 <= len(key) <= 512 or not key.isascii() or any(c.isspace() for c in key):
                return self.reply(400, {'error': 'API Key 格式不正确'})
            cfg = config()
            atomic(Path(cfg['api_key_file']).expanduser(), key)
        elif path != '/api/refresh':
            return self.reply(404, {'error': 'Not found'})
        result = command('start', '--no-block', 'meraki-split-preview.service')
        if result.returncode:
            return self.reply(500, {'error': '任务启动失败，请检查任务日志'})
        return self.reply(202, {'ok': True, 'message': '已启动地址库下载和差异预览，不会自动下发到 Meraki。'})


class ConsoleHTTPServer(ThreadingHTTPServer):
    """Keep slow TLS handshakes out of the accept loop."""
    daemon_threads = True
    request_queue_size = 64
    tls_context = None
    handshake_timeout = 10

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(self.handshake_timeout)
            if self.tls_context is not None:
                request = self.tls_context.wrap_socket(
                    request, server_side=True, do_handshake_on_connect=False)
                request.do_handshake()
            request.settimeout(30)
            self.finish_request(request, client_address)
        except (TimeoutError, socket.timeout, ConnectionError, ssl.SSLError):
            pass
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=os.environ.get('APP_BIND', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--local-http', action='store_true')
    args = parser.parse_args()
    if args.local_http and args.host != '127.0.0.1':
        raise SystemExit('Plain HTTP is restricted to loopback')
    init_auth()
    server = ConsoleHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    server.secure = not args.local_http
    server.allowed_hosts = set(os.environ.get('APP_ALLOWED_HOSTS', f'{args.host}:{args.port}').split(','))
    if args.host == '127.0.0.1':
        server.allowed_hosts.add(f'localhost:{args.port}')
    if server.secure:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(PRIVATE / 'gui-cert.pem', PRIVATE / 'gui-key.pem')
        server.tls_context = ctx
    from runtime import jobs
    jobs.start_scheduler()
    server.serve_forever()


if __name__ == '__main__':
    main()
