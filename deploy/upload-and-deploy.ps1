# Upload project to existing Hostinger VPS as isolated Docker app
# Does not touch other /docker projects. Uses shared-edge for HTTPS only.
# Usage:
#   .\deploy\upload-and-deploy.ps1 -VpsIp 200.97.171.119
# Or with SSH config host:
#   .\deploy\upload-and-deploy.ps1 -VpsIp hostinger-vps

param(
  [Parameter(Mandatory = $true)][string]$VpsIp,
  [string]$User = "root",
  [string]$RemoteDir = "/docker/webhook-infradealer"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "Packaging project..."
$tar = Join-Path $env:TEMP "webhook-infradealer-deploy.tgz"
if (Test-Path $tar) { Remove-Item $tar -Force }

Push-Location $Root
tar -czf $tar `
  --exclude=frontend/node_modules `
  --exclude=backend/.venv `
  --exclude=backend/infradealer.db `
  --exclude=.git `
  backend frontend docker-compose.yml .env.production
Pop-Location

Write-Host "Uploading to ${User}@${VpsIp}:${RemoteDir} ..."
ssh "$User@$VpsIp" "mkdir -p $RemoteDir"
scp $tar "${User}@${VpsIp}:/tmp/webhook-infradealer-deploy.tgz"
ssh "$User@$VpsIp" @"
set -euo pipefail
mkdir -p $RemoteDir
tar -xzf /tmp/webhook-infradealer-deploy.tgz -C $RemoteDir
cd $RemoteDir

# Append Caddy route once (do not rewrite existing hosts)
CF=/docker/shared-edge/Caddyfile
if ! grep -q 'webhook.infradealer.com' \"\$CF\"; then
  cp \"\$CF\" \"\$CF.bak-before-webhook-\$(date +%Y%m%d_%H%M%S)\"
  cat >> \"\$CF\" <<'EOF'

# --- InfraDealer WhatsApp webhook (do not remove) ---
webhook.infradealer.com {
	encode gzip
	reverse_proxy webhook-infradealer-web:80 {
		header_up X-Forwarded-Proto {scheme}
		header_up X-Real-IP {remote_host}
		header_up Host {host}
	}
}
EOF
  docker exec shared-edge caddy reload --config /etc/caddy/Caddyfile || docker restart shared-edge
fi

docker compose build
docker compose up -d
docker compose ps
echo 'Local health:'
curl -fsS http://127.0.0.1:9088/api/health || true
"@

Write-Host "Done. Check https://webhook.infradealer.com/api/health"
