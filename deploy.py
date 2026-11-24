#!/usr/bin/env python3
"""Owned-rule reconciliation with fresh backup, WAL and read-after-write checks."""
import argparse, collections, copy, datetime as dt, fcntl, hashlib, ipaddress, json, os
from pathlib import Path
import sites
import secrets, time, tarfile, urllib.request, urllib.error
from urllib.parse import urlencode
import backup
from preview import API, NoRedirect, atomic, dump
BASE=Path(__file__).resolve().parent

def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def read(p,default=None):return json.loads(p.read_text()) if p.exists() else ({} if default is None else default)
def paths(cfg):
 root=Path(cfg['state_dir']).expanduser()/'deployment';root.mkdir(parents=True,exist_ok=True);return root

def rule_key(rule):
 r=copy.deepcopy(rule)
 # Only normalize documented equivalent defaults, not unknown fields.
 if r.get('protocol')=='any':r.setdefault('port','any')
 src=r.get('source')
 if isinstance(src,dict) and src.get('cidr')=='any':src['cidr']='0.0.0.0/0'
 return digest(r)
def body(snapshot):return {k:copy.deepcopy(snapshot.get(k,[])) for k in ('custom','majorApplications','applications')}
def view_hash(snapshot):return digest(body(snapshot))
def credentials(cfg):
 p=Path(cfg['api_key_file']).expanduser()
 if p.stat().st_mode&0o077:raise ValueError('API Key 权限必须为 0600')
 return p.read_text().strip()
def live(cfg,key):
 nets=backup.fetch('/organizations/'+str(cfg['organization_id'])+'/networks',key)
 targets=[n for n in nets if sites.matches(n,cfg)]
 if len(targets)!=1:raise ValueError('目标网络必须唯一')
 nid=targets[0]['id'];q=urlencode({'networkIds[]':nid})
 data=backup.fetch('/organizations/'+str(cfg['organization_id'])+'/appliance/trafficShaping/vpnExclusions/byNetwork?'+q,key)
 rows=[x for x in data.get('items',[]) if x['networkId']==nid]
 if len(rows)!=1:raise ValueError('未读取到唯一排除规则集合')
 return rows[0]

def reconcile(snapshot,managed,desired):
 current=snapshot.get('custom',[])
 owned=collections.Counter(rule_key(x) for x in managed)
 available=collections.Counter(rule_key(x) for x in current)
 if any(available[k]<n for k,n in owned.items()):raise ValueError('软件管理规则在 Meraki 被删除或修改，停止下发，请人工核对')
 foreign=[];existing_owned=[]
 for r in current:
  k=rule_key(r)
  if owned[k]:existing_owned.append(r);owned[k]-=1
  else:foreign.append(r)
 foreign_keys={rule_key(r) for r in foreign}
 desired_map={rule_key(r):r for r in desired if rule_key(r) not in foreign_keys}
 remaining=set(desired_map);result=[];new_managed=[];removed=[]
 owned=collections.Counter(rule_key(x) for x in existing_owned)
 for r in current:
  k=rule_key(r)
  if owned[k]:
   owned[k]-=1
   if k in remaining:result.append(r);new_managed.append(r);remaining.remove(k)
   else:removed.append(r)
  else:result.append(r)
 added=[r for k,r in desired_map.items() if k in remaining]
 result.extend(added);new_managed.extend(added)
 payload=body(snapshot);payload['custom']=result
 return {'payload':payload,'managed':new_managed,'added':added,'removed':removed,'preserved_count':len(foreign),'satisfied_by_existing':sum(rule_key(r) in foreign_keys for r in desired)}

def schema_check(cfg,key):
 spec=backup.fetch('/organizations/'+str(cfg['organization_id'])+'/openapiSpec',key)
 op=spec['paths']['/networks/{networkId}/appliance/trafficShaping/vpnExclusions']['put']
 schema=op.get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema')
 if schema is None:schema=next(p['schema'] for p in op['parameters'] if p['in']=='body')
 props=schema['properties'];source=props['custom']['items']['properties'].get('source',{}).get('properties',{})
 if not {'cidr','vlanId','port'}<=set(source) or 'applications' not in props:raise ValueError('实时 API 定义未支持完整源字段/应用规则，禁止写入')
 return {'source_supported':True,'max_items':props['custom'].get('maxItems'),'schema_hash':digest(schema)}

def desired_rules(prefixes):
 networks=[]
 for p in prefixes:
  n=ipaddress.ip_network(p,strict=True)
  if n.version!=4 or not n.is_global:raise ValueError('国内地址库含非公网 IPv4，禁止下发')
  networks.append(n)
 return [{'protocol':'any','destination':str(n),'port':'any','source':{'cidr':'0.0.0.0/0','port':'any'}} for n in ipaddress.collapse_addresses(networks)]

def plan(cfg,prefixes=None,label='china'):
 root=paths(cfg);key=credentials(cfg);snapshot=live(cfg,key)
 if read(root/'pending-transaction.json'):raise ValueError('存在待核对事务，请先恢复核对')
 owner=read(root/'managed.json',{'rules':[]})
 if owner.get('network_id') and owner['network_id']!=snapshot['networkId']:raise ValueError('规则归属属于其他网络')
 report=read(Path(cfg['state_dir']).expanduser()/'latest.json')
 if prefixes is None:
  age=time.time()-dt.datetime.fromisoformat(report['generated_at_utc']).timestamp()
  if age>172800:raise ValueError('国内地址库超过 48 小时，请先更新')
  prefixes=report['china_prefixes']
 desired=desired_rules(prefixes);change=reconcile(snapshot,owner['rules'],desired)
 blockers=[]
 if owner['rules'] and len(change['removed'])/len(owner['rules'])>.15 and label=='china':blockers.append('单次删除超过软件规则的 15%，需人工检查地址库')
 schema=schema_check(cfg,key)
 if schema['max_items'] and len(change['payload']['custom'])>schema['max_items']:blockers.append('超出 API 声明的规则容量')
 if len(change['payload']['custom'])>10000:blockers.append('超出软件安全上限 10000 条')
 pid=secrets.token_hex(16)
 p={'id':pid,'created_at':now(),'expires_at':time.time()+1800,'network_id':snapshot['networkId'],'label':label,
    'before':snapshot,'before_hash':view_hash(snapshot),'owner_hash':digest(owner),'schema':schema,
    'source_hash':report.get('source_sha256'),'blockers':blockers,**change}
 dump(root/'plans'/ (pid+'.json'),p)
 summary={'id':pid,'created_at':p['created_at'],'expires_at':p['expires_at'],'network_id':p['network_id'],'add':len(p['added']),'remove':len(p['removed']),
   'preserved':p['preserved_count'],'managed_after':len(p['managed']),'total_after':len(p['payload']['custom']),
   'satisfied_by_existing':p['satisfied_by_existing'],'blockers':blockers,'schema':schema,
   'sample_additions':p['added'][:20],'sample_removals':p['removed'][:20],'noop':not p['added'] and not p['removed']}
 dump(root/'latest-plan.json',summary);dump(root/'plan-status.json',{'status':'success','time':now(),'id':pid});return summary

def matches(actual,expected):
 # Rule ordering can be normalized by Meraki; compare full multiset including source.
 for k in ('custom','majorApplications','applications'):
  fn=rule_key if k=='custom' else digest
  if collections.Counter(fn(r) for r in actual.get(k,[]))!=collections.Counter(fn(r) for r in expected.get(k,[])):return False
 return True

def put(nid,payload,key):
 req=urllib.request.Request(API+'/networks/'+nid+'/appliance/trafficShaping/vpnExclusions',
  data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-Cisco-Meraki-API-Key':key},method='PUT')
 with urllib.request.build_opener(NoRedirect()).open(req,timeout=90) as r:return json.load(r)

def commit(root,transaction,current):
 wanted=collections.Counter(rule_key(r) for r in transaction['managed'])
 rules=[]
 for r in current['custom']:
  k=rule_key(r)
  if wanted[k]:rules.append(r);wanted[k]-=1
 if any(wanted.values()):raise ValueError('软件归属核对失败')
 dump(root/'managed.json',{'network_id':transaction['network_id'],'rules':rules,'updated_at':now()})
 dump(root/'last-success.json',{'time':now(),'plan_id':transaction['plan_id'],'label':transaction.get('label'),'backup':transaction['backup'],
   'managed_count':len(rules),'readback_hash':view_hash(current)})
 if transaction.get('label')=='china':dump(root/'last-china-success.json',read(root/'last-success.json'))
 (root/'pending-transaction.json').unlink(missing_ok=True)

def recover(cfg):
 root=paths(cfg);t=read(root/'pending-transaction.json')
 if not t:return
 current=live(cfg,credentials(cfg))
 if matches(current,t['payload']):commit(root,t,current)
 elif matches(current,t['before']):(root/'pending-transaction.json').unlink()
 else:raise ValueError('待核对事务与现网不一致；停止自动下发，需要人工检查备份')

def archive_context(folder, owner, p):
    # Preserve ownership as well as Meraki data for a cross-server recovery.
    context={'managed_before':owner,'managed_after':p['managed'],'plan_id':p['id'],
             'network_id':p['network_id'],'before_hash':p['before_hash'],
             'target_payload':p['payload'],'source_hash':p.get('source_hash')}
    dump(folder/'deployment-context.json',context)
    manifest=read(folder/'manifest.json')
    manifest['deployment_context_sha256']=hashlib.sha256((folder/'deployment-context.json').read_bytes()).hexdigest()
    dump(folder/'manifest.json',manifest)
    archive=folder.parent/(folder.name+'.tar.gz')
    tmp=archive.with_suffix('.tmp')
    with tarfile.open(tmp,'w:gz') as tar:tar.add(folder,arcname=folder.name)
    os.replace(tmp,archive)
    atomic(folder.parent/(folder.name+'.tar.gz.sha256'),hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    latest=read(folder.parent/'latest.json')
    if latest.get('id')==folder.name:dump(folder.parent/'latest.json',manifest)

def apply(cfg,pid):
 root=paths(cfg)
 with (root/'.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  recover(cfg)
  p=read(root/'plans'/(pid+'.json'))
  if not p or p['expires_at']<time.time():raise ValueError('预览已过期，请重新生成')
  if p['blockers']:raise ValueError('；'.join(p['blockers']))
  owner=read(root/'managed.json',{'rules':[]})
  if digest(owner)!=p['owner_hash']:raise ValueError('软件规则状态已变化，请重新预览')
  key=credentials(cfg);current=live(cfg,key)
  if view_hash(current)!=p['before_hash']:raise ValueError('Meraki 配置已变化，请重新预览；未提交')
  if not p['added'] and not p['removed']:return {'status':'no_change','message':'无变化，未发送 PUT'}
  dump(root/'status.json',{'status':'backing_up','time':now(),'plan_id':pid})
  archive=backup.capture(cfg,'before-deploy-'+pid)
  manifest=backup.verify(archive)
  # Device-wide unavailable sections never waive backup of the exact edited collection.
  exclusions=[e for e in manifest['entries'] if e['operation']=='vpnExclusions' and e['status']=='ok']
  if len(exclusions)!=1:raise ValueError('排除规则备份失败，禁止下发')
  snapshot=read(archive/exclusions[0]['file'])
  rows=[r for r in snapshot.get('items',[]) if r['networkId']==p['network_id']]
  if len(rows)!=1 or view_hash(rows[0])!=p['before_hash']:raise ValueError('备份期间现网改变，禁止下发，请重新预览')
  # Only already audited unsupported product endpoints may be unavailable.
  allowed={'getDeviceCellularSims','getNetworkApplianceSecurityIntrusion','getNetworkApplianceSecurityMalware','getNetworkApplianceSingleLan','getNetworkFirmwareUpgradesStagedGroups','getNetworkFirmwareUpgradesStagedStages','getNetworkMqttBrokers','getNetworkVlanProfiles','getNetworkVlanProfilesAssignmentsByDevice'}
  for e in manifest['entries']:
   if e['status']!='ok' and not (e.get('http_status')==400 and e['operation'].split('-')[0] in allowed and any(w in e.get('error_detail','').lower() for w in ['not support','only supports','only available'])):
    raise ValueError('存在未确认备份失败项，禁止下发')
  archive_context(archive,owner,p)
  schema_check(cfg,key)
  if view_hash(live(cfg,key))!=p['before_hash']:raise ValueError('提交前检测到外部修改，请重新预览')
  t={'plan_id':pid,'label':p['label'],'network_id':p['network_id'],'before':p['before'],'payload':p['payload'],'managed':p['managed'],'backup':str(archive),'time':now()}
  dump(root/'pending-transaction.json',t);dump(root/'status.json',{'status':'submitting','time':now(),'plan_id':pid})
  error=None
  try:put(p['network_id'],p['payload'],key)
  except urllib.error.HTTPError as exc:
   try:message=str(json.loads(exc.read(8192)).get('errors',[])).replace(key,'[REDACTED]')[:1000]
   except Exception:message=''
   error=f'HTTP {exc.code}: {message}'
  except Exception as exc:error=type(exc).__name__+'：请求结果不确定'
  actual=live(cfg,key)
  for attempt in range(3):
   if matches(actual,p['payload']):break
   time.sleep(2)
   actual=live(cfg,key)
  if matches(actual,p['payload']):
   commit(root,t,actual)
   return {'status':'success','added':len(p['added']),'removed':len(p['removed']),'preserved':p['preserved_count'],'managed':len(p['managed']),'backup':str(archive),'message':'已下发并读回核对'}
  if matches(actual,p['before']) and error and error.startswith('HTTP 4'):
   (root/'pending-transaction.json').unlink(missing_ok=True)
   raise ValueError((error or '读回仍为原配置')+'；已确认原配置保留，未更新软件归属')
  raise ValueError((error or '读回与目标不一致')+'；已保留事务及备份，禁止自动重试/覆盖，请人工核对')

def queue(cfg,pid):
 if len(pid)!=32 or any(c not in '0123456789abcdef' for c in pid):raise ValueError('无效预览 ID')
 root=paths(cfg)
 if not (root/'plans'/(pid+'.json')).exists():raise ValueError('预览不存在')
 fd=os.open(root/'queued.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
 with os.fdopen(fd,'w') as f:json.dump({'id':pid},f)

def main():
 os.umask(0o077);parser=argparse.ArgumentParser();parser.add_argument('--plan',action='store_true');parser.add_argument('--apply');parser.add_argument('--queued',action='store_true');parser.add_argument('--auto',action='store_true');args=parser.parse_args()
 cfg=read(Path(os.environ.get('APP_CONFIG', str(BASE/'config.json'))));root=paths(cfg)
 try:
  if args.plan:print(json.dumps(plan(cfg)));return
  if args.auto:
   if not read(root/'settings.json').get('automatic',False):return
   with (root/'.lock').open('w') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    recover(cfg)
   pid=plan(cfg)['id']
  else:pid=read(root/'queued.json')['id'] if args.queued else args.apply
  result=apply(cfg,pid);result['time']=now();dump(root/'status.json',result);print(json.dumps(result))
 except Exception as exc:
  message=str(exc) if isinstance(exc,(ValueError,RuntimeError,BlockingIOError)) else type(exc).__name__
  dump(root/('plan-status.json' if args.plan else 'status.json'),{'status':'failed','error':message,'time':now()});raise SystemExit(message)
 finally:
  if args.queued:(root/'queued.json').unlink(missing_ok=True)
if __name__=='__main__':main()
