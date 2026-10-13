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


class FakeRedis:
    """Just enough of redis-py for the card ledger."""

    def __init__(self):
        self.kv, self.lists = {}, {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.lists.pop(k, None)

    def lpush(self, k, v):
        self.lists.setdefault(k, []).insert(0, v)

    def ltrim(self, k, a, b):
        self.lists[k] = self.lists.get(k, [])[a:b + 1]

    def expire(self, k, ttl):
        pass

    def pipeline(self):
        return self

    def execute(self):
        pass


@pytest.fixture()
def fake_redis(monkeypatch):
    from app.ai import card_ledger

    fake = FakeRedis()
    monkeypatch.setattr(card_ledger, "get_redis", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret-key-for-integration")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://webhook.infradealer.com")
