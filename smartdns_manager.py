#!/usr/bin/env python3
"""Validated China domain feeds and SmartDNS activation. No Meraki access."""
import datetime as dt, fcntl, hashlib, ipaddress, json, os
from pathlib import Path
import re, signal, subprocess, time, urllib.request, urllib.error
from preview import atomic, dump
from dns_isolation import verify as verify_isolation
POLICY_VERSION=3
ROOT=Path(os.environ.get('SMARTDNS_STATE', str(Path.home()/'.local/state/unity-smartdns')))
SOURCE='https://raw.githubusercontent.com/felixonmars/dnsmasq-china-list/master/accelerated-domains.china.conf'
FEED_API='https://api.github.com/repos/felixonmars/dnsmasq-china-list/contents/accelerated-domains.china.conf'
ADDRESS=os.environ.get('SMARTDNS_ADDRESS', '127.0.0.1')
PORT=int(os.environ.get('SMARTDNS_PORT', '53'))

def read(p,default=None):return json.loads(p.read_text()) if p.exists() else ({} if default is None else default)
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def normalize(domain):
 if not isinstance(domain,str):raise ValueError('域名必须是文本')
 domain=domain.strip().rstrip('.').lower()
 if domain.startswith('*.'):domain=domain[2:]
 try:domain=domain.encode('idna').decode('ascii')
 except UnicodeError:raise ValueError('域名格式无效')
 if len(domain)>253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',s) for s in domain.split('.')):raise ValueError('域名格式无效：'+domain[:80])
 try:ipaddress.ip_address(domain)
 except ValueError:return domain
 raise ValueError('请填写域名，不是 IP')

def parse_source(raw):
 domains=set()
 for line in raw.decode('utf-8').splitlines():
  line=line.strip()
  if not line or line.startswith('#'):continue
  m=re.fullmatch(r'server=/([^/]+)/[^/]+',line)
  if not m:raise ValueError('域名源格式异常，保留旧列表')
  domains.add(normalize(m[1]))
 if len(domains)<1000:raise ValueError('域名源数量异常，保留旧列表')
 return sorted(domains)

def download():
 errors=[]
 for url in (FEED_API,SOURCE):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'UnityNetech-SmartDNS/1.0','Accept':'application/vnd.github.raw+json'})
   with urllib.request.urlopen(req,timeout=25) as r:raw=r.read(10_000_001)
   if len(raw)>10_000_000:raise ValueError('下载内容过大')
   parse_source(raw)
   return raw
  except (OSError,ValueError) as exc:errors.append(type(exc).__name__)
 raise ValueError('官方域名源下载失败：'+', '.join(errors))

def custom_domains(text):
 if not isinstance(text,str) or len(text)>100000:raise ValueError('自定义列表过长')
 rows=sorted({normalize(x) for x in text.splitlines() if x.strip() and not x.strip().startswith('#')})
 if len(rows)>1000:raise ValueError('自定义域名最多 1000 条')
 return rows

def config(folder,port,host,log):
 return f'''server-name UnityNetech-DNS
bind {host}:{port}
bind-tcp {host}:{port}
cache-size 4096
speed-check-mode none
dualstack-ip-selection no
serve-expired yes
ca-file /etc/ssl/certs/ca-certificates.crt
log-level notice
log-file {log}
log-size 128k
log-num 2
server-https https://1.1.1.1/dns-query
server-https https://1.0.0.1/dns-query
server-https https://223.5.5.5/dns-query -group china -exclude-default-group
server-https https://1.12.12.12/dns-query -group china -exclude-default-group
domain-set -name china -type list -file {folder}/china-domains.txt
nameserver /domain-set:china/china
nameserver /cn/china
nameserver /xn--fiqs8s/china
nameserver /xn--fiqz9s/china
'''

def query(host,port,name,tcp=False):
 args=['/usr/bin/dig','@'+host,'-p',str(port),name,'A','+time=3','+tries=1','+noall','+comments','+answer']
 if tcp:args.append('+tcp')
 r=subprocess.run(args,capture_output=True,text=True,timeout=5)
 valid=r.returncode==0 and 'status: NOERROR' in r.stdout and re.search(r'\sIN\s+A\s+',r.stdout)
 return bool(valid)

def health(host,port):
 for _ in range(3):
  try:
   if all(query(host,port,n) for n in ['www.baidu.com','example.com']):return
  except subprocess.TimeoutExpired:pass
  time.sleep(1)
 raise ValueError('国内或默认 DNS 组健康检查失败，保留上一版')

def pid():
 r=subprocess.run(['systemctl','show','unity-smartdns.service','-p','MainPID','--value'],capture_output=True,text=True,timeout=5)
 return int(r.stdout.strip() or '0')

def restart():
 if os.environ.get("CONTAINER_RUNTIME") == "1":
  from runtime import jobs
  return jobs.request_dns_restart()
 old=pid()
 if not old:return False
 details=subprocess.run(['systemctl','show','unity-smartdns.service','-p','User','-p','ExecStart'],capture_output=True,text=True,timeout=5).stdout
 if 'User=user\n' not in details or 'path=/usr/sbin/smartdns ' not in details:raise ValueError('服务进程不匹配')
 os.kill(old,signal.SIGTERM)
 for _ in range(30):
  time.sleep(.3)
  new=pid()
  if new and new!=old:return True
 raise ValueError('SmartDNS 服务未自动重启')

def update():
 ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
 with (ROOT/'.lock').open('w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  previous=read(ROOT/'state.json');intent=read(ROOT/'custom-pending.json')
  custom=intent.get('domains',read(ROOT/'custom.json',[]))
  custom=[normalize(x) for x in custom]
  dump(ROOT/'update-status.json',{'status':'running','started_at':now()})
  try:
   raw=download()
   domains=parse_source(raw)
   if previous.get('source_count') and len(domains)<previous['source_count']*.8:raise ValueError('域名数量减少超过20%，请人工检查')
   combined=sorted(set(domains)|set(custom));checksum=hashlib.sha256(('\n'.join(combined)+'\n').encode()).hexdigest()
   if previous.get('sha256')==checksum and previous.get('policy_version')==POLICY_VERSION and (ROOT/'current').exists():
    previous['last_checked']=now();previous['custom_count']=len(custom);dump(ROOT/'state.json',previous);dump(ROOT/'custom.json',custom)
    if intent and read(ROOT/'custom-pending.json')==intent:(ROOT/'custom-pending.json').unlink(missing_ok=True)
    dump(ROOT/'update-status.json',{'status':'success','changed':False,'time':now()});return
   folder=ROOT/'versions'/dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ');folder.mkdir(parents=True)
   atomic(folder/'china-domains.txt','\n'.join(combined)+'\n')
   atomic(folder/'source-domains.txt','\n'.join(domains)+'\n')
   atomic(folder/'probe.conf',config(folder,6053,'127.0.0.1',folder/'probe.log'))
   atomic(folder/'smartdns.conf',config(folder,PORT,ADDRESS,ROOT/'smartdns.log'))
   with (folder/'probe-output.log').open('w') as log:
    process=subprocess.Popen(['/usr/sbin/smartdns','-f','-c',str(folder/'probe.conf'),'-p','-'],stdout=log,stderr=log)
    try:health('127.0.0.1',6053)
    finally:
     process.terminate()
     try:process.wait(timeout=5)
     except subprocess.TimeoutExpired:process.kill();process.wait()
   isolation=verify_isolation(folder)
   current=ROOT/'current';old=current.resolve() if current.exists() else None
   temp=ROOT/'current.next';temp.unlink(missing_ok=True);temp.symlink_to(folder);os.replace(temp,current)
   try:
    if restart():health(ADDRESS,PORT)
   except Exception:
    if old:
     temp.symlink_to(old);os.replace(temp,current)
     try:restart();health(ADDRESS,PORT)
     except Exception:pass
    raise
   record={'policy_version':POLICY_VERSION,'isolation':isolation,'source':SOURCE,'source_count':len(domains),'custom_count':len(custom),'count':len(combined),'sha256':checksum,'updated_at':now(),'last_checked':now(),'address':ADDRESS,'source_sha256':hashlib.sha256(raw).hexdigest(),'version':folder.name}
   dump(ROOT/'state.json',record);dump(ROOT/'custom.json',custom)
   if intent and read(ROOT/'custom-pending.json')==intent:(ROOT/'custom-pending.json').unlink(missing_ok=True)
   dump(ROOT/'update-status.json',{'status':'success','changed':True,'time':now()})
   print(json.dumps({'source_count':len(domains),'total':len(combined),'status':'success'}))
  except Exception as exc:
   message=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
   dump(ROOT/'update-status.json',{'status':'failed','error':message,'time':now(),'previous_version_preserved':True})
   raise SystemExit(message)

def state():
 value=read(ROOT/'state.json');value['update']=read(ROOT/'update-status.json');value['custom']=read(ROOT/'custom.json',[])
 if os.environ.get('CONTAINER_RUNTIME') == '1':
  try: value['active']=query('127.0.0.1',PORT,'example.com')
  except (OSError,subprocess.TimeoutExpired): value['active']=False
 else:
  r=subprocess.run(['systemctl','is-active','unity-smartdns.service'],capture_output=True,text=True,timeout=5)
  value['active']=r.stdout.strip()=='active'
 return value

def search(q,page):
 if len(q)>253:raise ValueError('搜索内容过长')
 path=ROOT/'current/china-domains.txt'
 rows=path.read_text().splitlines() if path.exists() else []
 matches=[x for x in rows if q.lower().strip() in x];pages=max(1,(len(matches)+79)//80);page=min(max(0,page),pages-1)
 return {'total':len(rows),'matched':len(matches),'page':page,'pages':pages,'rows':matches[page*80:page*80+80]}

if __name__=='__main__':
 os.umask(0o077);update()
