#!/bin/sh
set -e
cd /docker/webhook-infradealer
tar -xzf /tmp/idw-admin-contacts-broadcast.tgz
docker compose build api web
docker compose up -d --force-recreate --no-deps api web
sleep 5
curl -fsS http://127.0.0.1:9088/api/health
echo
