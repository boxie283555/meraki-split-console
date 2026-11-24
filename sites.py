"""Independent site configuration; default site retains legacy state and credentials."""
import fcntl
import json
import os
from pathlib import Path
import re
import secrets


BASE = Path(__file__).resolve().parent

def default_path():
    return Path(os.environ.get('APP_CONFIG', str(BASE/'config.json')))

def root():
    cfg=json.loads(default_path().read_text())
    return Path(cfg['state_dir']).expanduser()/'sites'

def validate_id(site):
    if site != 'default' and not re.fullmatch(r'[a-f0-9]{24}', site or ''):
        raise ValueError('无效站点 ID')
    return site

def config_path(site='default'):
    validate_id(site)
    return default_path() if site=='default' else root()/site/'config.json'

def get(site='default'):
    p=config_path(site)
    if not p.is_file(): raise ValueError('站点不存在')
    cfg=json.loads(p.read_text())
    cfg['_site_id']=site
    return cfg

def _known_id(cfg):
    p=Path(cfg["state_dir"]).expanduser()/"latest.json"
    try:return json.loads(p.read_text()).get("network_id", "")
    except (FileNotFoundError,ValueError):return ""

def listing():
    ids=['default']
    if root().exists():
        ids += sorted(p.name for p in root().iterdir() if p.is_dir() and re.fullmatch(r'[a-f0-9]{24}',p.name) and (p/'config.json').exists())
    rows=[]
    for site in ids:
        cfg=get(site);key=Path(cfg['api_key_file']).expanduser()
        rows.append({'id':site,'name':cfg.get('display_name',cfg['network_name']),
                     'organization_id':cfg['organization_id'],'network_name':cfg['network_name'],
                     'network_id':cfg.get('network_id') or _known_id(cfg), 'api_key_configured':key.is_file() and key.stat().st_size>0})
    return rows

def validate_key(key):
    if not isinstance(key,str) or not 20<=len(key)<=512 or not key.isascii() or any(c.isspace() for c in key):
        raise ValueError('API Key 格式不正确')
    return key

def create(obj):
    from preview import atomic
    org=obj.get('organization_id','');name=obj.get('network_name','');nid=obj.get('network_id','');label=obj.get('name','')
    if not isinstance(org,str) or not re.fullmatch(r'[0-9]{1,32}',org):raise ValueError('组织 ID 应为数字')
    if not isinstance(name,str) or not 1<=len(name.strip())<=128:raise ValueError('请填写准确的网络名称')
    if not isinstance(label,str) or len(label)>128:raise ValueError('站点名称过长')
    if not isinstance(nid,str) or (nid and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',nid)):raise ValueError('无效 Network ID')
    key=validate_key(obj.get('key',''))
    # Read-only validation binds immutable network identity before saving a site.
    from preview import pages
    try:
        networks=pages('/organizations/'+org+'/networks?perPage=1000',key)
        targets=[n for n in networks if (n.get('id')==nid if nid else n.get('name')==name.strip())]
        if len(targets)!=1:raise ValueError('该组织下未找到唯一的目标网络，请核对名称或Network ID')
        nid=targets[0]['id'];name=targets[0]['name']
    except ValueError:raise
    except Exception:raise ValueError('无法验证站点，请检查API Key、组织ID、来源白名单和网络连接')
    folder=root();folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (folder/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        rows=listing()
        if len(rows)>=50:raise ValueError('最多支持50个站点')
        if any(str(r['organization_id'])==org and (r['network_name']==name.strip() or (nid and r['network_id']==nid)) for r in rows):
            raise ValueError('该组织和网络已存在，请切换到已有站点')
        site=secrets.token_hex(12);dest=folder/site;dest.mkdir(mode=0o700)
        cfg={'display_name':label.strip() or name.strip(),'organization_id':org,'network_name':name.strip(),
             'api_key_file':str(dest/'api-key'),'state_dir':str(dest/'state')}
        if nid:cfg['network_id']=nid
        atomic(dest/'api-key',key)
        atomic(dest/'config.json',json.dumps(cfg,ensure_ascii=False))
    return site

def matches(network,cfg):
    if cfg.get('network_id'): return network.get('id')==cfg['network_id']
    return network.get('name')==cfg['network_name']
