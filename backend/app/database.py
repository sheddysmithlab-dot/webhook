from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def migrate_schema() -> None:
    """create_all does not add/widen columns on existing tables."""
    pg = [
        "ALTER TABLE chats ALTER COLUMN timestamp_ms TYPE BIGINT",
        "ALTER TABLE meta_settings ALTER COLUMN system_user_token TYPE VARCHAR(512)",
        "ALTER TABLE meta_settings ADD COLUMN IF NOT EXISTS ai_enabled BOOLEAN DEFAULT TRUE",
        "ALTER TABLE meta_settings ADD COLUMN IF NOT EXISTS ai_api_key VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE meta_settings ADD COLUMN IF NOT EXISTS ai_api_base VARCHAR(300) DEFAULT 'https://api.openai.com/v1'",
        "ALTER TABLE meta_settings ADD COLUMN IF NOT EXISTS ai_model VARCHAR(80) DEFAULT 'gpt-4o-mini'",
        "ALTER TABLE meta_settings ADD COLUMN IF NOT EXISTS ai_reply_language VARCHAR(16) DEFAULT 'auto'",
        "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS language VARCHAR(16) DEFAULT ''",
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS media_id INTEGER",
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS photo_ids TEXT DEFAULT '[]'",
        "ALTER TABLE ai_listing_drafts ADD COLUMN IF NOT EXISTS confirmed_json TEXT DEFAULT '{}'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(128) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS account_ready BOOLEAN DEFAULT FALSE",
    ]
    sqlite = [
        "ALTER TABLE meta_settings ADD COLUMN ai_enabled BOOLEAN DEFAULT 1",
        "ALTER TABLE meta_settings ADD COLUMN ai_api_key VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE meta_settings ADD COLUMN ai_api_base VARCHAR(300) DEFAULT 'https://api.openai.com/v1'",
        "ALTER TABLE meta_settings ADD COLUMN ai_model VARCHAR(80) DEFAULT 'gpt-4o-mini'",
        "ALTER TABLE meta_settings ADD COLUMN ai_reply_language VARCHAR(16) DEFAULT 'auto'",
        "ALTER TABLE ai_conversations ADD COLUMN language VARCHAR(16) DEFAULT ''",
        "ALTER TABLE chats ADD COLUMN media_id INTEGER",
        "ALTER TABLE products ADD COLUMN photo_ids TEXT DEFAULT '[]'",
        "ALTER TABLE ai_listing_drafts ADD COLUMN confirmed_json TEXT DEFAULT '{}'",
        "ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN password_hash VARCHAR(128) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN account_ready BOOLEAN DEFAULT 0",
    ]
    stmts = sqlite if settings.database_url.startswith("sqlite") else pg
    with engine.begin() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception:
                continue


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
