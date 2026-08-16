import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, SessionLocal, engine
from .routers import admin, catalog, webhook
from .services import get_or_create_settings

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
app.include_router(catalog.router)
app.include_router(admin.router)
app.include_router(webhook.router)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        get_or_create_settings(db)
        db.commit()
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"ok": True, "service": "infradealer"}
