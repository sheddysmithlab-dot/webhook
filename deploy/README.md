# Production deploy — existing Hostinger VPS + isolated Docker

## Target
- Domain: `webhook.infradealer.com`
- Path on VPS: `/docker/webhook-infradealer`
- Stack: shared-edge Caddy (SSL) + Nginx frontend + FastAPI + Postgres
- WhatsApp recipient: `8224000826`
- **Does not** bind host 80/443 and **does not** modify other `/docker/*` apps

## Hostinger steps
1. DNS A record: `webhook` → existing VPS IP (`200.97.171.119`)
2. From Windows (SSH config host `hostinger-vps` ok):

```powershell
.\deploy\upload-and-deploy.ps1 -VpsIp hostinger-vps
```

Or manually upload into `/docker/webhook-infradealer` and run `./deploy/deploy.sh`.

## After DNS is live
Update Meta webhook callback to:
`https://webhook.infradealer.com/webhook/whatsapp`

Verify token: same as `META_VERIFY_TOKEN` in `.env.production`

Register / connect WhatsApp business number ending in `8224000826` in Meta → Step 2 Production setup.

## Useful
```bash
docker compose logs -f api
docker compose exec db psql -U infradealer -d infradealer
curl https://webhook.infradealer.com/api/health
```
