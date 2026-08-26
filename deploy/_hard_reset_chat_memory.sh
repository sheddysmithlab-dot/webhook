#!/usr/bin/env bash
# Hard-reset webhook AI chat memory, then fresh redeploy containers.
# Preserves: users, contacts, meta_settings, blocked_numbers,
#            infradealer_integration, infradealer_account_states, products.
set -euo pipefail

APP_DIR="${APP_DIR:-/docker/webhook-infradealer}"
DOMAIN="${DOMAIN:-webhook.infradealer.com}"

echo "==> HARD RESET + FRESH DEPLOY ${DOMAIN}"
cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "ERROR: missing ${APP_DIR}/.env"
  exit 1
fi

cp -a .env /tmp/webhook-infradealer.env.hard-reset-bak

# Read DB credentials without sourcing full .env (values may contain spaces/special chars)
DB_USER="$(grep -E '^POSTGRES_USER=' .env | head -1 | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'")"
DB_NAME="$(grep -E '^POSTGRES_DB=' .env | head -1 | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'")"
DB_USER="${DB_USER:-infradealer}"
DB_NAME="${DB_NAME:-infradealer}"

echo "==> Sync code from origin/main"
git fetch origin main
git reset --hard origin/main
cp -a /tmp/webhook-infradealer.env.hard-reset-bak .env

echo "==> Ensure stack is up for DB/redis"
docker compose up -d db redis
sleep 3

echo "==> Flush Redis chat/session cache"
docker compose exec -T redis redis-cli FLUSHDB || true

echo "==> Truncate AI chat-memory tables (keep accounts/settings) as ${DB_USER}/${DB_NAME}"
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
BEGIN;

-- Drop AI conversation / listing memory
TRUNCATE TABLE
  ai_events,
  ai_agent_memory,
  ai_media,
  ai_listing_drafts,
  ai_conversations
RESTART IDENTITY CASCADE;

-- Clear live WhatsApp chat threads used by memory extractors
TRUNCATE TABLE
  chats,
  messages,
  otps
RESTART IDENTITY CASCADE;

-- Clear stale outbound / callback queue state
TRUNCATE TABLE
  infradealer_callbacks,
  infradealer_requests,
  infradealer_outbox
RESTART IDENTITY CASCADE;

COMMIT;

SELECT 'ai_conversations=' || count(*) FROM ai_conversations;
SELECT 'ai_listing_drafts=' || count(*) FROM ai_listing_drafts;
SELECT 'chats=' || count(*) FROM chats;
SELECT 'users_kept=' || count(*) FROM users;
SELECT 'account_states_kept=' || count(*) FROM infradealer_account_states;
SQL

echo "==> Fresh rebuild + recreate"
chmod +x deploy/deploy.sh
./deploy/deploy.sh

echo "==> Post-reset sanity"
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT 'ai_conversations' AS t, count(*) FROM ai_conversations UNION ALL SELECT 'chats', count(*) FROM chats;"
curl -fsS "https://${DOMAIN}/api/health" || true
echo
echo HARD_RESET_DEPLOY_OK
