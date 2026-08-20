import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import InfraDealerIntegration


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-integration")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://webhook.infradealer.com")
