#!/bin/sh
set -e
cd /docker/webhook-infradealer
tar -xzf /tmp/idw-dedup-callback.tgz
docker compose build api
docker compose up -d --force-recreate --no-deps api
sleep 8
curl -fsS http://127.0.0.1:9088/api/health
echo
