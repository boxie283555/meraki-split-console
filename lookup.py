"""Bounded system DNS and official RIR RDAP lookups; no shell/user URL fetching."""
from concurrent.futures import ThreadPoolExecutor
import functools
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from preview import NoRedirect, atomic

REGISTRIES = {'rdap.apnic.net', 'rdap.arin.net', 'rdap.db.ripe.net', 'rdap.lacnic.net', 'rdap.afrinic.net'}
CACHE = Path.home() / '.cache/meraki-split-preview/rdap'
MUTEX = threading.Lock()
OWNERS = {}
DNS_CODE = '''import socket,json,sys
mode,value=json.loads(sys.stdin.read())
try:
 if mode=='forward':
  result=sorted(set(x[4][0] for x in socket.getaddrinfo(value,None,0,socket.SOCK_STREAM)))
 else:
  host,aliases,_=socket.gethostbyaddr(value);result=sorted(set([host]+aliases))
 print(json.dumps({'values':result}))
except (socket.gaierror,socket.herror) as e:
 print(json.dumps({'values':[],'error':'DNS 未找到记录或解析服务不可用'}))
'''


def normalize(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 253:
        raise ValueError('请输入一个有效域名或 IPv4 / IPv6 地址')
    value = value.strip()
    try:
        ip = ipaddress.ip_address(value)
        if '%' in value:
            raise ValueError('不支持带 zone ID 的地址')
        return 'ip', str(ip)
    except ValueError:
        if ':' in value or '/' in value or re.fullmatch(r'[0-9.]+', value):
            raise ValueError('请输入域名或有效 IP，不要包含 URL、端口或网段掩码')
    try:
        domain = value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise ValueError('域名格式无效')
    if len(domain) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.')):
        raise ValueError('域名格式无效')
    return 'domain', domain


def dns(mode, value):
    try:
        r = subprocess.run([sys.executable, '-c', DNS_CODE], input=json.dumps([mode, value]),
                           capture_output=True, text=True, timeout=7)
        return json.loads(r.stdout) if r.returncode == 0 else {'values': [], 'error': 'DNS 查询失败'}
    except (subprocess.TimeoutExpired, ValueError):
        return {'values': [], 'error': 'DNS 查询超时或返回无效'}


def fetch_json(url):
    # URLs are built only from IANA bootstrap or the fixed IANA source.
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in REGISTRIES | {'data.iana.org'} or parsed.port not in (None,443):
        raise ValueError('非官方 RDAP 服务地址')
    request = urllib.request.Request(url, headers={'Accept': 'application/rdap+json, application/json', 'User-Agent': 'UnityNetech-IP-Lookup/1.0'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=8) as r:
        data = r.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('RDAP response too large')
        return json.loads(data)


def bootstrap(version):
    path = CACHE / f'ipv{version}.json'
    with MUTEX:
        if path.exists() and time.time() - path.stat().st_mtime < 604800:
            return json.loads(path.read_text())
        try:
            data = fetch_json(f'https://data.iana.org/rdap/ipv{version}.json')
            if not isinstance(data.get('services'), list):
                raise ValueError('Invalid bootstrap')
            atomic(path, json.dumps(data))
            return data
        except Exception:
            if path.exists():
                return json.loads(path.read_text())
            raise


def registry_url(ip, data):
    matches = []
    for blocks, urls in data['services']:
        for block in blocks:
            network = ipaddress.ip_network(block)
            if network.version == ip.version and ip in network:
                for url in urls:
                    p = urlsplit(url)
                    if p.scheme == 'https' and p.hostname in REGISTRIES and p.port in (None, 443):
                        matches.append((network.prefixlen, url.rstrip('/') + '/ip/' + str(ip)))
    if not matches:
        raise ValueError('未找到对应注册机构')
    return max(matches)[1]


def parse_owner(data, url):
    organizations = []
    def scan(entities):
        for e in entities:
            if 'registrant' in e.get('roles', []):
                card = e.get('vcardArray', [])
                for item in card[1] if len(card) > 1 and isinstance(card[1], list) else []:
                    if len(item) >= 4 and item[0] in ('fn', 'org'):
                        v = item[3]
                        if isinstance(v, list):
                            v = ' / '.join(str(x) for x in v)
                        if isinstance(v, str) and v.strip() and v not in organizations:
                            organizations.append(v)
            scan(e.get('entities', []))
    scan(data.get('entities', []))
    return {'status': 'ok', 'owner': ' / '.join(organizations) or data.get('name') or data.get('handle') or '注册机构未披露',
            'owner_basis': 'registrant' if organizations else 'network_record',
            'network_name': data.get('name', ''), 'country': data.get('country', ''),
            'range': f"{data.get('startAddress', '')} – {data.get('endAddress', '')}",
            'source': url, 'registry': urlsplit(url).hostname}


def owner(value):
    ip = ipaddress.ip_address(value)
    if not ip.is_global:
        return {'status': 'not_public', 'owner': '私有或特殊用途地址，无公网注册 Owner'}
    with MUTEX:
        cached = OWNERS.get(value)
        if cached and time.time() - cached[0] < 86400:
            return cached[1]
    try:
        url = registry_url(ip, bootstrap(ip.version))
        result = parse_owner(fetch_json(url), url)
        with MUTEX:
            if len(OWNERS) >= 512:
                OWNERS.pop(next(iter(OWNERS)))
            OWNERS[value] = (time.time(), result)
        return result
    except urllib.error.HTTPError as exc:
        return {'status': 'error', 'owner': '暂未获取', 'error': f'RDAP HTTP {exc.code}'}
    except Exception:
        return {'status': 'error', 'owner': '暂未获取', 'error': '注册机构查询超时或不可达，请稍后重试'}


def lookup(value):
    kind, query = normalize(value)
    resolved = {'values': [query]} if kind == 'ip' else dns('forward', query)
    addresses = sorted(set(resolved['values']), key=lambda v: (ipaddress.ip_address(v).version, int(ipaddress.ip_address(v))))
    def row(value):
        with ThreadPoolExecutor(max_workers=2) as pool:
            ptr_future = pool.submit(dns, 'reverse', value)
            who_future = pool.submit(owner, value)
            ptr, who = ptr_future.result(), who_future.result()
        return {'ip': value, 'version': ipaddress.ip_address(value).version,
                'ptr': ptr['values'], 'ptr_error': ptr.get('error', ''), **who}
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(row, addresses[:32]))
    return {'query': query, 'kind': kind, 'rows': rows, 'total': len(addresses),
            'truncated': len(addresses) > 32, 'dns_error': resolved.get('error', ''),
            'resolver': '平台所在主机的系统 DNS', 'queried_at': time.time()}
