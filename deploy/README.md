# Production deploy — existing Hostinger VPS + isolated Docker + GitHub

## Repo
https://github.com/sheddysmithlab-dot/webhook

## Target
- Domain: `webhook.infradealer.com`
- Path on VPS: `/docker/webhook-infradealer`
- Stack: shared-edge Caddy (SSL) + Nginx frontend + FastAPI + Postgres
- WhatsApp recipient: `8224000826`
- **Does not** bind host 80/443 and **does not** modify other `/docker/*` apps

## First-time Hostinger steps
1. DNS A record: `webhook` → existing VPS IP (`200.97.171.119`)
2. On VPS:

```bash
git clone https://github.com/sheddysmithlab-dot/webhook.git /docker/webhook-infradealer
cd /docker/webhook-infradealer
cp .env.production.example .env   # fill Meta + Postgres secrets
chmod +x deploy/deploy.sh
./deploy/deploy.sh
```

## Update from GitHub
```bash
cd /docker/webhook-infradealer
git pull origin main
./deploy/deploy.sh
```

Or from Windows (packaging fallback):

```powershell
.\deploy\upload-and-deploy.ps1 -VpsIp hostinger-vps
```

## After DNS is live
Update Meta webhook callback to:
`https://webhook.infradealer.com/webhook/whatsapp`

Verify token: same as `META_VERIFY_TOKEN` in `.env`

## Useful
```bash
docker compose logs -f api
docker compose exec db psql -U infradealer -d infradealer
curl https://webhook.infradealer.com/api/health
```
