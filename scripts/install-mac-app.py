"""Create a local macOS launcher; app data stays in Docker's named volume."""
from pathlib import Path
import plistlib,shlex,shutil
root=Path(__file__).resolve().parents[1]
app=Path.home()/'Applications/Meraki Console.app'
if app.exists():raise SystemExit('Launcher already exists; keep or remove it explicitly before reinstalling.')
mac=app/'Contents/MacOS';mac.mkdir(parents=True)
resources=app/'Contents/Resources';resources.mkdir()
shutil.copy2(root/'packaging/macos/assets/MerakiConsole.icns',resources/'MerakiConsole.icns')
(app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIconFile':'MerakiConsole.icns','CFBundleName':'Meraki Console','CFBundleIdentifier':'local.unitynetech.merakiconsole','CFBundleVersion':'1.0','CFBundleExecutable':'launch','CFBundlePackageType':'APPL'}))
(mac/'launch').write_text('#!/bin/sh\nexec /usr/bin/open -a Terminal '+shlex.quote(str(root/'scripts/start-mac.command'))+'\n')
(mac/'launch').chmod(0o755)
print(app)
