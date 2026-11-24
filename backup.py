#!/usr/bin/env python3
"""Read-only network configuration archive and offline recovery plan."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sites
import re
import tarfile
import time
import urllib.error
from urllib.parse import quote, urlencode, urljoin
from preview import API, get, dump, atomic

BASE=Path(__file__).resolve().parent

def fetch(path,key):
    url=API+path;result=None;seen=set()
    while url:
        if url in seen or len(seen)>100:raise ValueError('Pagination loop')
        seen.add(url)
        for attempt in range(3):
            try:
                raw,headers=get(url,key);break
            except urllib.error.HTTPError as e:
                if e.code not in (429,500,502,503,504) or attempt==2:raise
                time.sleep(min(10,max(1,int(e.headers.get('Retry-After','2')))))
        obj=json.loads(raw)
        if result is None:result=obj
        elif isinstance(result,list) and isinstance(obj,list):result.extend(obj)
        elif isinstance(result,dict) and isinstance(obj,dict) and isinstance(result.get('items'),list):result['items'].extend(obj['items'])
        else:raise ValueError('Unsupported pagination shape')
        url=None
        for item in headers.get('Link','').split(','):
            if 'rel="next"' in item:url=urljoin(API+'/',item.split('>')[0].strip().lstrip('<'))
        time.sleep(.22)
    return result

def root_for(cfg):return Path(cfg['state_dir']).expanduser()/'backups'

def capture(cfg,reason='manual'):
    root=root_for(cfg);root.mkdir(parents=True,exist_ok=True)
    with (root/'.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _capture(cfg,root,reason)

def _capture(cfg,root,reason):
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    folder=root/stamp;folder.mkdir(mode=0o700)
    manifest={'id':stamp,'reason':reason,'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'scope':cfg['network_name']+' network and member devices; organization settings reference only','entries':[],'limitations':['Not a vendor-importable full-device image.','Masked passwords, certificates/private keys and GUI-only settings need separate recovery material.','Organization, template, policy-object and SGP/MN hub dependencies are not automatically restorable.','API snapshots are sequential, not an atomic Dashboard snapshot.','This archive does not execute restore. Rule deployment is separately guarded in deploy.py.'],'status':'running'}
    status=root/'status.json'
    def progress():dump(status,{'id':stamp,'status':manifest['status'],'completed':len(manifest['entries'])})
    progress()
    try:
        keypath=Path(cfg['api_key_file']).expanduser()
        if keypath.stat().st_mode & 0o077:raise ValueError('Key file must be 0600')
        key=keypath.read_text().strip()
        org=quote(str(cfg['organization_id']),safe='')
        networks=fetch('/organizations/'+org+'/networks',key)
        targets=[n for n in networks if sites.matches(n,cfg)]
        if len(targets)!=1:raise ValueError('Network name not unique')
        network=targets[0];nid=network['id'];manifest['network_id']=nid;manifest['network_name']=network['name'];manifest['organization_id']=org
        products=set(network.get('productTypes',[]));saved={}
        catalog=json.loads((BASE/'backup-catalog.json').read_text())
        def save(path,operation,meta=None):
            filename=f'{len(manifest["entries"]):03d}-{operation}.json'
            e={'path':path,'operation':operation,'file':filename,'restore_put':(meta or {}).get('restore_put'),'put_fields':(meta or {}).get('put_fields',[]),'put_required':(meta or {}).get('put_required',[])}
            try:
                data=fetch(path,key);dump(folder/filename,data);saved[path]=data
                e.update(status='ok',sha256=hashlib.sha256((folder/filename).read_bytes()).hexdigest())
            except urllib.error.HTTPError as exc:
                e.update(status='unavailable',http_status=exc.code)
                try:
                    details=json.loads(exc.read(8192)).get('errors', [])
                    e['error_detail']=str(details).replace(key,'[REDACTED]')[:800]
                except Exception:
                    pass
            except Exception as exc:e.update(status='failed',error=type(exc).__name__)
            manifest['entries'].append(e);dump(folder/'manifest.json',manifest);progress()
            return saved.get(path)
        devices=save('/networks/'+nid+'/devices','getNetworkDevices') or []
        for m in catalog:
            p=m['path'];params=set(re.findall(r'{(.*?)}',p))
            if m['family'] not in products|{'networks','devices'}:continue
            if params=={'networkId'}:
                path=p.replace('{networkId}',quote(nid,safe=''))
                if path not in saved:save(path,m['operation'],m)
            elif params=={'serial'}:
                for device in devices:
                    serial=device['serial'];family=device.get('productType')
                    if not family:family='appliance' if device.get('model','').startswith('MX') else None
                    if m['family'] not in {'devices',family}:continue
                    save(p.replace('{serial}',quote(serial,safe='')),m['operation']+'-'+serial,m)
        # Resource-detail GETs are expanded only using IDs returned by their collection.
        fields={'vlanId':'id','portId':'number','number':'number','staticRouteId':'id','rfProfileId':'id','staticDelegatedPrefixId':'id','customPerformanceClassId':'id','service':'service','groupPolicyId':'groupPolicyId'}
        pending=[]
        for m in catalog:
            if m['family'] not in products|{'networks','devices'}:continue
            p=m['path'].replace('{networkId}',nid)
            unknown=re.findall(r'{(.*?)}',p)
            if len(unknown)!=1 or unknown[0] not in fields:continue
            param=unknown[0];parent=p.split('/{'+param+'}')[0];rows=saved.get(parent)
            if not isinstance(rows,list):continue
            for row in rows:
                value=row.get(fields[param],row.get('id'))
                if value is not None:pending.append((p.replace('{'+param+'}',quote(str(value),safe='')),m))
        for p,m in pending:save(p,m['operation'],m)
        for suffix,name in [('appliance/vpn/thirdPartyVPNPeers','organizationThirdPartyVPNPeers'),('appliance/vpn/vpnFirewallRules','organizationVPNFirewallRules')]:
            save('/organizations/'+org+'/'+suffix,name)
        exclusion=save('/organizations/'+org+'/appliance/trafficShaping/vpnExclusions/byNetwork?'+urlencode({'networkIds[]':nid}),'vpnExclusions')
        manifest['unexpanded_paths']=[m['path'] for m in catalog if m['family'] in products|{'networks','devices'} and not any(e['operation'].startswith(m['operation']) for e in manifest['entries'])]
        failures=[e for e in manifest['entries'] if e['status']!='ok']
        manifest['status']='captured_with_gaps' if failures or manifest['unexpanded_paths'] else 'captured'
        manifest['successful']=sum(e['status']=='ok' for e in manifest['entries']);manifest['unavailable']=len(failures)
        manifest['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        manifest['safe_for_unattended_restore']=False
        dump(folder/'manifest.json',manifest)
        generate_plan(folder)
        archive=root/(stamp+'.tar.gz')
        with tarfile.open(archive,'w:gz') as tar:tar.add(folder,arcname=stamp)
        atomic(root/(stamp+'.tar.gz.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
        dump(root/'latest.json',manifest);progress()
        print(json.dumps({'id':stamp,'status':manifest['status'],'successful':manifest['successful'],'unavailable':manifest['unavailable']}))
        return folder
    except Exception as exc:
        manifest.update(status='failed',error=type(exc).__name__)
        dump(folder/'manifest.json',manifest);progress();raise

def verify(folder):
    m=json.loads((folder/'manifest.json').read_text())
    for e in m['entries']:
        if e['status']=='ok':
            p=folder/e['file']
            if p.parent!=folder or hashlib.sha256(p.read_bytes()).hexdigest()!=e['sha256']:raise ValueError('Backup integrity check failed')
    if m.get('deployment_context_sha256'):
        if hashlib.sha256((folder/'deployment-context.json').read_bytes()).hexdigest()!=m['deployment_context_sha256']:raise ValueError('Ownership backup integrity check failed')
    return m

def generate_plan(folder):
    m=verify(folder);plan=[]
    for e in m['entries']:
        if e['status']!='ok':continue
        data=json.loads((folder/e['file']).read_text())
        if e.get('restore_put') and isinstance(data,dict):
            body={k:v for k,v in data.items() if k in e['put_fields']}
            plan.append({'method':'PUT','path':e['path'],'body':body,'review_required':True,'missing_required':[k for k in e['put_required'] if k not in body]})
        else:plan.append({'path':e['path'],'backup_file':e['file'],'manual_recovery_required':True})
    dump(folder/'restore-plan.json',{'network_id':m['network_id'],'executable':False,'warning':'Review schemas, masked secrets and dependency order. Do not submit raw GET responses.','steps':plan})
    atomic(folder/'RESTORE.md',(BASE/'RESTORE.md').read_text())

def backup_before_change(cfg,candidate):
    """Legacy whole-config guard. Scoped exclusion deployment uses deploy.apply."""
    folder=capture(cfg,'before-change');m=verify(folder)
    if m['status']!='captured':raise RuntimeError('Backup has gaps: unattended changes blocked')
    dump(folder/'proposed-change.json',candidate)
    raise RuntimeError('Backup prepared; Meraki writes are not implemented/enabled')

if __name__=='__main__':
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--verify');parser.add_argument('--plan');args=parser.parse_args()
    if args.verify:verify(Path(args.verify));print('Integrity OK')
    elif args.plan:generate_plan(Path(args.plan));print('Offline plan generated; no Meraki writes')
    else:capture(json.loads((Path(os.environ.get('APP_CONFIG', str(BASE/'config.json')))).read_text()))
