#!/usr/bin/env bash
# Remote deploy for webhook.infradealer.com — pulled by GitHub Actions after push to main.
set -euo pipefail

APP_DIR="${APP_DIR:-/docker/webhook-infradealer}"
DOMAIN="${DOMAIN:-webhook.infradealer.com}"

echo "==> Deploying ${DOMAIN} in ${APP_DIR}"
cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "ERROR: missing ${APP_DIR}/.env — aborting to protect secrets"
  exit 1
fi

# Keep production env safe across hard resets
cp -a .env /tmp/webhook-infradealer.env.deploy-bak

git fetch origin main
git reset --hard origin/main

cp -a /tmp/webhook-infradealer.env.deploy-bak .env

chmod +x deploy/deploy.sh
./deploy/deploy.sh

echo "==> Public health"
curl -fsS "https://${DOMAIN}/api/health" || true
echo
echo DEPLOY_OK
