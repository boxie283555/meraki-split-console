#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"
resources="$(cd "$(dirname "$0")" && pwd)"
project="$HOME/Library/Application Support/Meraki Console/project"
fail() { echo "$1"; echo '按回车退出。'; read -r answer; exit 1; }
trap 'echo "启动未完成，请查看上方提示。按回车退出。"; read -r answer' ERR
if ! command -v docker >/dev/null; then
 open 'https://www.docker.com/products/docker-desktop/'
 fail '请先安装并启动 Docker Desktop，再重新打开 Meraki Console。'
fi
if ! docker info >/dev/null 2>&1; then
 if [ -d /Applications/Docker.app ]; then
  open -a Docker
  for i in {1..60}; do docker info >/dev/null 2>&1 && break; sleep 2; done
 elif command -v colima >/dev/null; then
  colima start --cpu 2 --memory 2 --disk 20
 else
  fail 'Docker 引擎未运行。请启动 Docker Desktop 或 Colima 后重试。'
 fi
fi
docker info >/dev/null 2>&1 || fail 'Docker 引擎尚未就绪。'
if docker compose version >/dev/null 2>&1; then compose=(docker compose)
elif command -v docker-compose >/dev/null; then compose=(docker-compose)
else fail '缺少 Docker Compose，请安装完整 Docker Desktop 或 brew install docker-compose。'; fi
mkdir -p "$project"
# Update only shipped files. Never delete private runtime files or overwrite .env.
/usr/bin/rsync -r --exclude='.env' "$resources/project/" "$project/"
cd "$project"
if [ ! -f .env ]; then
 cp .env.example .env
 chmod 600 .env
 echo '首次安装：请在打开的配置文件中填写 MERAKI_ORG_ID 和 MERAKI_NETWORK_NAME，然后保存。'
 echo '也可以暂不填写，仅体验页面；已有数据卷的组织配置会保持原样。'
 open -e "$project/.env"
 echo '保存后按回车继续。'
 read -r answer
fi
# Fixed project name preserves the same named volume as the source-based installation.
"${compose[@]}" -p meraki-split-console up -d --build
ready=0
for i in {1..60}; do
 if /usr/bin/curl -kfsS --max-time 2 https://localhost:8443/ >/dev/null; then ready=1; break; fi
 sleep 2
done
[ "$ready" = 1 ] || fail '网页尚未就绪，可运行 docker logs meraki-split-console-console-1 查看原因。'
echo '启动成功：https://localhost:8443'
echo '默认账号：admin。以下为本机初始密码（如果你已修改密码，以修改后的为准）：'
"${compose[@]}" -p meraki-split-console exec -T console sh -c 'if [ -f /data/private/gui-initial-password ]; then cat /data/private/gui-initial-password; else echo "初始密码文件已移除，请使用已保存的密码。"; fi'
open 'https://localhost:8443'
echo '关闭本终端窗口不会停止应用。停止应用请运行：'
echo 'docker stop meraki-split-console-console-1'
