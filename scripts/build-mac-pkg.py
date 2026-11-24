#!/usr/bin/env python3
"""Build an unsigned, credential-free macOS installer from explicit source files."""
import argparse,hashlib,plistlib,shutil,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--version',default='1.1.1');args=p.parse_args()
if not all(c.isdigit() or c=='.' for c in args.version):p.error('Version must be numeric dotted format')
out=ROOT/'dist';out.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='meraki-pkg-') as tmp:
 tmp=Path(tmp);payload=tmp/'payload';app=payload/'Applications/Meraki Console.app'
 resources=app/'Contents/Resources';mac=app/'Contents/MacOS';project=resources/'project'
 project.mkdir(parents=True);mac.mkdir(parents=True)
 names=['Dockerfile','compose.yaml','compose.dns.yaml','.env.example','.dockerignore','README.md','RESTORE.md','backup-catalog.json','config.example.json','gui.py','sites.py','runtime.py','bootstrap.py','preview.py','backup.py','deploy.py','lookup.py','smartdns_manager.py','dns_isolation.py']
 for name in names:shutil.copy2(ROOT/name,project/name)
 for name in ['web','docs']:shutil.copytree(ROOT/name,project/name,ignore=shutil.ignore_patterns('._*','.DS_Store','__pycache__'))
 shutil.copy2(ROOT/'packaging/macos/launch',mac/'launch')
 shutil.copy2(ROOT/'packaging/macos/start.command',resources/'start.command')
 (mac/'launch').chmod(0o755);(resources/'start.command').chmod(0o755)
 shutil.copy2(ROOT/'packaging/macos/assets/MerakiConsole.icns',resources/'MerakiConsole.icns')
 info={'CFBundleIconFile':'MerakiConsole.icns','CFBundleName':'Meraki Console','CFBundleDisplayName':'Meraki Console','CFBundleIdentifier':'com.unitynetech.merakiconsole','CFBundleVersion':args.version,'CFBundleShortVersionString':args.version,'CFBundleExecutable':'launch','CFBundlePackageType':'APPL','LSMinimumSystemVersion':'12.0','NSAppleEventsUsageDescription':'Open Terminal to start the local Docker application.'}
 (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
 subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
 subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
 component=tmp/'components.plist'
 subprocess.run(['pkgbuild','--analyze','--root',str(payload),str(component)],check=True)
 data=plistlib.loads(component.read_bytes())
 for item in data:item['BundleIsRelocatable']=False
 component.write_bytes(plistlib.dumps(data))
 package=out/f'Meraki-Console-{args.version}.pkg'
 subprocess.run(['pkgbuild','--root',str(payload),'--component-plist',str(component),'--identifier','com.unitynetech.merakiconsole.pkg','--version',args.version,'--install-location','/','--ownership','recommended',str(package)],check=True)
 digest=hashlib.sha256(package.read_bytes()).hexdigest()
 (out/(package.name+'.sha256')).write_text(digest+'  '+package.name+'\n')
 print(package)
