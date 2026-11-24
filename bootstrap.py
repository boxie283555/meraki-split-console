"""Initialize private container state without contacting Meraki."""
import json,os,subprocess
from pathlib import Path
os.umask(0o077)
data=Path(os.environ.get('APP_DATA','/data'))
private=data/'private';private.mkdir(parents=True,exist_ok=True)
config=Path(os.environ.get('APP_CONFIG','/data/config.json'))
if not config.exists():
 config.write_text(json.dumps({'organization_id':os.environ.get('MERAKI_ORG_ID','YOUR_ORGANIZATION_ID'),'network_name':os.environ.get('MERAKI_NETWORK_NAME','YOUR_NETWORK_NAME'),'api_key_file':str(private/'api-key'),'state_dir':str(data/'state')},indent=2))
if not (private/'gui-key.pem').exists():
 hosts=os.environ.get('APP_ALLOWED_HOSTS','localhost:8443,127.0.0.1:8443').split(',')
 import ipaddress
 names=[]
 for host in hosts:
  host=host.rsplit(':',1)[0]
  try:ipaddress.ip_address(host);names.append('IP:'+host)
  except ValueError:names.append('DNS:'+host)
 subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','365','-subj','/CN=Meraki Console','-addext','subjectAltName='+','.join(names),'-keyout',str(private/'gui-key.pem'),'-out',str(private/'gui-cert.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
# Interrupted read-only jobs must not leave the UI permanently busy.
for statusfile in [data/'dns/update-status.json']:
 if statusfile.exists():
  status=json.loads(statusfile.read_text())
  if status.get('status')=='running':
   status.update(status='failed',error='任务因应用重启中断，请重新更新')
   statusfile.write_text(json.dumps(status,ensure_ascii=False))
os.execv('/usr/local/bin/python',['python','/app/gui.py'])
