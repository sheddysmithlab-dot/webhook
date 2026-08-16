#!/usr/bin/env bash
# Deploy isolated webhook-infradealer on existing VPS.
# Safe: never touches other compose projects; only appends one Caddy host if missing.
set -euo pipefail

APP_DIR="${APP_DIR:-/docker/webhook-infradealer}"
DOMAIN="${DOMAIN:-webhook.infradealer.com}"
CF="/docker/shared-edge/Caddyfile"

echo "==> Deploying ${DOMAIN} into ${APP_DIR}"
cd "$APP_DIR"

if ! grep -q 'webhook.infradealer.com' "$CF"; then
  echo "==> Adding ${DOMAIN} route to shared-edge Caddyfile (append only)"
  cp "$CF" "$CF.bak-before-webhook-$(date +%Y%m%d_%H%M%S)"
  cat >> "$CF" <<'EOF'

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
  docker exec shared-edge caddy reload --config /etc/caddy/Caddyfile \
    || docker restart shared-edge
else
  echo "==> Caddy route already present"
fi

docker compose build
docker compose up -d
docker compose ps

echo "==> Local health check"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:9088/api/health" >/dev/null 2>&1; then
    echo "OK http://127.0.0.1:9088/api/health"
    break
  fi
  sleep 3
done

echo "==> Public health (may need DNS/SSL propagate)"
curl -fsS "https://${DOMAIN}/api/health" || true
