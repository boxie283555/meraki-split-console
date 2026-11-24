#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null; then
 echo '请先安装 Docker Desktop，或 brew install colima docker docker-compose'; exit 1
fi
if ! docker info >/dev/null 2>&1; then
 if command -v colima >/dev/null; then colima start --cpu 2 --memory 2 --disk 20 --vm-type vz
 else open -a Docker; for i in {1..60}; do docker info >/dev/null 2>&1 && break; sleep 2; done; fi
fi
if docker compose version >/dev/null 2>&1; then docker compose up -d --build
else docker-compose up -d --build; fi
open 'https://localhost:8443'
