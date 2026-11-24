#!/usr/bin/env python3
"""Read-only China IPv4/Meraki exclusion preview. No configuration write API."""
import argparse
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request

APNIC = 'https://ftp.apnic.net/stats/apnic/delegated-apnic-latest'
API = 'https://api.meraki.com/api/v1'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API credential to a redirect target.


def get(url, key=None):
    headers = {'User-Agent': 'UnityNetech-Meraki-ReadOnly-Preview/1.0'}
    if key:
        if not url.startswith(API + '/'):
            raise ValueError('Authenticated URL outside configured official API')
        headers['X-Cisco-Meraki-API-Key'] = key
    req = urllib.request.Request(url, headers=headers, method='GET')
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=60) as r:
        data = r.read(20_000_001)
        if len(data) > 20_000_000:
            raise ValueError('Response exceeds size limit')
        return data, r.headers


def pages(path, key, field=None):
    url, result, seen = API + path, [], set()
    while url:
        if url in seen or len(seen) >= 100:
            raise ValueError('Invalid API pagination')
        seen.add(url)
        raw, headers = get(url, key)
        obj = json.loads(raw)
        rows = obj[field] if field else obj
        if not isinstance(rows, list):
            raise ValueError('Unexpected API response shape')
        result.extend(rows)
        url = None
        for link in headers.get('Link', '').split(','):
            if 'rel="next"' in link:
                url = link.split('>')[0].strip().lstrip('<')
                url = urllib.parse.urljoin(API + '/', url)
    return result


def china_prefixes(raw):
    networks = []
    for line in raw.decode('utf-8').splitlines():
        p = line.split('|')
        if len(p) < 7 or p[0] != 'apnic' or p[1] != 'CN' or p[2] != 'ipv4':
            continue
        if p[6] not in ('allocated', 'assigned'):
            continue
        first, count = ipaddress.IPv4Address(p[3]), int(p[4])
        if count <= 0:
            raise ValueError('Invalid allocation size')
        networks.extend(ipaddress.summarize_address_range(
            first, ipaddress.IPv4Address(int(first) + count - 1)))
    merged = list(ipaddress.collapse_addresses(networks))
    if not merged:
        raise ValueError('No valid China IPv4 allocations; retain previous report')
    return [str(n) for n in merged]


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def dump(path, obj):
    atomic(path, json.dumps(obj, indent=2, ensure_ascii=False) + '\n')


def run(config, root):
    raw, _ = get(APNIC)
    prefixes = china_prefixes(raw)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime('%Y%m%dT%H%M%S.%fZ')
    out = root / 'runs' / stamp
    out.mkdir(parents=True)
    previous_path = root / 'latest.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    old = set(previous.get('china_prefixes', []))
    report = {
        'mode': 'READ_ONLY', 'generated_at_utc': now.isoformat(),
        'source': APNIC, 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'china_prefix_count': len(prefixes), 'china_prefixes': prefixes,
        'source_delta': {'baseline_exists': bool(previous),
                         'added': sorted(set(prefixes) - old),
                         'removed': sorted(old - set(prefixes))},
        'meraki_status': 'not_configured', 'deployment_allowed': False,
        'notes': [
            'APNIC CN allocation country is not a guarantee of physical hosting location.',
            'IPv4 only; no domain list is imported.',
            'Capacity and source/VLAN API round-trip compatibility remain unverified.',
            'Existing rules are never deleted or written by this program.',
        ],
    }
    atomic(out / 'china-ipv4.txt', '\n'.join(prefixes) + '\n')
    atomic(out / 'apnic-source.txt', raw.decode('utf-8'))
    key_path = Path(config['api_key_file']).expanduser()
    if key_path.exists() and key_path.stat().st_size:
        if key_path.stat().st_mode & 0o077:
            raise ValueError('API key file must have permissions 0600')
        key = key_path.read_text().strip()
        org = urllib.parse.quote(str(config['organization_id']), safe='')
        networks = pages('/organizations/' + org + '/networks?perPage=1000', key)
        matches = [n for n in networks if n['name'] == config['network_name']]
        if len(matches) != 1:
            raise ValueError('Target network name must match exactly one network')
        network = matches[0]
        query = urllib.parse.urlencode({'networkIds[]': network['id'], 'perPage': 1000})
        entries = pages('/organizations/' + org +
                        '/appliance/trafficShaping/vpnExclusions/byNetwork?' + query,
                        key, 'items')
        matches = [e for e in entries if e['networkId'] == network['id']]
        if len(matches) != 1:
            raise ValueError('Expected exactly one network exclusion result')
        current = matches[0]
        dump(out / 'meraki-api-snapshot.json', current)
        # Only exact unrestricted IPv4 matches are counted. Do not assume an
        # enclosing prefix or unknown source field is equivalent to all clients.
        exact = {r.get('destination') for r in current.get('custom', [])
                 if r.get('protocol') == 'any' and r.get('port') in ('any', '*')
                 and set(r) <= {'protocol', 'destination', 'port'}}
        report.update(meraki_status='read_success', network_id=network['id'],
                      existing_custom_count=len(current.get('custom', [])),
                      exact_destination_matches=len(set(prefixes) & exact),
                      candidate_destinations_without_exact_match=sorted(set(prefixes) - exact))
        report['notes'].append('API snapshot is not a verified full restore backup; GUI source/VLAN fields may be absent.')
    dump(out / 'report.json', report)
    summary = ('# UnityNetech Meraki split preview\n\n'
               f"Mode: READ ONLY\n\nUTC: {report['generated_at_utc']}\n\n"
               f"China IPv4 prefixes: {len(prefixes)}\n\n"
               f"Meraki: {report['meraki_status']}\n\n"
               f"Source additions/removals: {len(report['source_delta']['added'])}/{len(report['source_delta']['removed'])}\n\n"
               + '\n'.join('- ' + n for n in report['notes']) + '\n')
    atomic(out / 'report.md', summary)
    dump(previous_path, report)
    atomic(root / 'latest.md', summary)
    dump(root / 'status.json', {'ok': True, 'run': str(out), 'time': now.isoformat()})
    print(summary)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=os.environ.get('APP_CONFIG', str(Path(__file__).with_name('config.json'))))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    root = Path(config['state_dir']).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            run(config, root)
        except Exception as exc:
            # Do not print request objects, credentials or response bodies.
            error = type(exc).__name__
            if isinstance(exc, urllib.error.HTTPError):
                error += ': HTTP ' + str(exc.code)
            elif isinstance(exc, ValueError):
                error += ': ' + str(exc)
            dump(root / 'status.json', {'ok': False, 'error': error,
                 'time': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'previous_report_preserved': True})
            raise SystemExit(error)


if __name__ == '__main__':
    main()
