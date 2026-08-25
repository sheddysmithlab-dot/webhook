# InfraDealer — Auto Deploy (git push → live)

Har repo ke `main` pe push (kisi bhi device se) GitHub Actions trigger karta hai aur production deploy ho jata hai.

| Repo | Site | Trigger | Workflow |
|------|------|---------|----------|
| `webhook` | https://webhook.infradealer.com | push `main` | `.github/workflows/deploy-vps.yml` |
| `Infra_backend` | https://api.infradealer.com | push `main` | `.github/workflows/deploy-vps.yml` |
| `Infra_frontend` | https://infradealer.com | push `main` | `.github/workflows/deploy-hostinger.yml` |
| `Infra_admin_panel` | https://admin.infradealer.com | push `main` | `.github/workflows/deploy-hostinger.yml` |
| `Infra_admin_office` | https://office.infradealer.com | push `main` | `.github/workflows/deploy-hostinger.yml` |

Manual run: GitHub → Actions → workflow → **Run workflow**.

## GitHub Secrets (zaroori)

### VPS repos (`webhook`, `Infra_backend`)

Settings → Secrets and variables → Actions:

| Secret | Example / meaning |
|--------|-------------------|
| `VPS_SSH_HOST` | `200.97.171.119` |
| `VPS_SSH_USER` | `root` |
| `VPS_SSH_KEY` | Deploy private key (full PEM, including `BEGIN`/`END`) |

VPS pe ye key `authorized_keys` mein honi chahiye. Webhook folder `/docker/webhook-infradealer` pe `git fetch origin main` chalna chahiye (deploy key / HTTPS token).

### Hostinger repos (`Infra_frontend`, `Infra_admin_panel`, `Infra_admin_office`)

| Secret | Example / meaning |
|--------|-------------------|
| `HOSTINGER_SSH_HOST` | Hostinger SSH host |
| `HOSTINGER_SSH_USER` | Hostinger SSH user (e.g. `u808821982`) |
| `HOSTINGER_SSH_PORT` | e.g. `65002` |
| `HOSTINGER_SSH_KEY` | Hostinger private key PEM |
| `VITE_API_URL` | `https://api.infradealer.com/api` |
| `VITE_SERVER_URL` | `https://api.infradealer.com` (frontend only) |

## Usage (kisi bhi PC se)

```bash
git add -A
git commit -m "your message"
git push origin main
```

Phir GitHub Actions tab mein green check wait karo. Local SSH/manual deploy ki zarurat nahi.

## Notes

- Sirf `main` branch auto-deploy karti hai.
- Concurrent pushes pe pehle wala cancel ho sakta hai (`concurrency`).
- Secrets missing hon to workflow fail hoga — pehle table wale secrets set karo.
