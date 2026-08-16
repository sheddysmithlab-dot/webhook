# Infradealer — WhatsApp → Product Card

React (Vite) frontend + Python FastAPI backend. Incoming WhatsApp messages hit a real webhook, get parsed, and can be published as public product cards after OTP.

Empty database on first run — no seed / demo listings.

## Local run

**Backend** (port 8000):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend** (port 5173):

```bash
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173/**

Vite proxies `/api` and `/webhook` to the Python server.

## Pages

| Path | Purpose |
|---|---|
| `/` | Public listing (published cards only) |
| `/list` | Prefill / direct publish form + OTP |
| `/meta` | Webhook settings, inbox, broadcast |
| `/admin` | Ledger: chats, messages, OTPs, products, users, blocks |

## WhatsApp webhook

Callback URL (Meta console):

`http://127.0.0.1:8000/webhook/whatsapp`

- **GET** — Meta verify challenge (`hub.mode`, `hub.verify_token`, `hub.challenge`)
- **POST** — incoming messages (signature checked if App Secret is saved)

Save Phone Number ID + System User Token to send OTP, test messages, and broadcasts through Graph API.

Without Meta credentials:

- OTP is hashed in SQLite; the code is printed only in the **backend terminal**
- Use **Inbound Message Save** on `/meta` to test parse → form → publish locally

## Data

SQLite file: `backend/infradealer.db`

OTP codes are stored as SHA-256 hashes (never shown in the UI).
