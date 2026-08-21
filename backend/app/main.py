import logging
import os
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .database import Base, SessionLocal, engine, migrate_schema
from .ai.memory import seed_memory
from .auth import SessionGate
from .contacts import sync_contacts_from_chats
from .infradealer.worker import run_integration_tasks
from .identity import repair_posted_listings
from .routers import admin, auth, catalog, infradealer, webhook
from .services import get_or_create_settings

log = logging.getLogger("infradealer")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="infradealer", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in (
            settings.frontend_origin
            + ",http://localhost:5173,http://127.0.0.1:5173,https://webhook.infradealer.com,https://infradealer.com,https://www.infradealer.com"
        ).split(",")
        if o.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionGate)
app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(admin.router)
app.include_router(infradealer.admin_router)
app.include_router(infradealer.callback_router)
app.include_router(webhook.router)


def _integration_loop() -> None:
    while True:
        # 5s poll so approve/reject status → WhatsApp stays under ~5 seconds even without callback
        time.sleep(5)
        db = SessionLocal()
        try:
            run_integration_tasks(db)
        except Exception:
            log.exception("integration background loop failed")
        finally:
            db.close()


@app.on_event("startup")
def startup():
    os.makedirs(settings.ai_media_dir, exist_ok=True)
    for attempt in range(6):
        try:
            Base.metadata.create_all(bind=engine)
            migrate_schema()
            break
        except SQLAlchemyError as exc:
            log.warning("schema init attempt %s: %s", attempt + 1, exc)
            time.sleep(0.4 * (attempt + 1))
    db = SessionLocal()
    try:
        get_or_create_settings(db)
        seed_memory(db)
        repair_posted_listings(db)
        sync_contacts_from_chats(db)
        try:
            run_integration_tasks(db, outbox_limit=50, poll_limit=20)
        except Exception:
            log.exception("outbox worker on startup")
        db.commit()
    finally:
        db.close()
    thread = threading.Thread(target=_integration_loop, name="infradealer-integration", daemon=True)
    thread.start()


@app.get("/api/health")
def health():
    return {"ok": True, "service": "infradealer"}
