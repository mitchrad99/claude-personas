"""
Global test configuration for the AAO CRM.

Sets DATABASE_URL to an in-memory SQLite database with StaticPool before
importing app or models — no Supabase connection is ever used in tests.
All Flask routes require HTTP Basic Auth; use the `auth` fixture for headers.

Why SQLite in-memory? All 8 models use only portable SQLAlchemy types
(Integer, String, Text, Date, DateTime, Boolean). No JSONB/ARRAY/UUID.
StaticPool ensures every SQLAlchemy Session in the test process shares the
same in-memory connection, so data written by one session is visible to all.
"""
import base64
import os
import sys

# ── env vars must be set before any import of app or models ──────────────────
os.environ.setdefault("CRM_PASSWORD", "testpass")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
# match_slack_users.py exits at import time if these are absent
os.environ.setdefault("SUPABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-token")

# Add scripts/ to sys.path so test_scripts.py can import them by name
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import models  # noqa: E402 - imported after env vars are set

# Override the module-level engine with a StaticPool in-memory instance.
# Because get_session() and init_db() resolve `engine`/`SessionLocal` from
# models.__dict__ at call time, patching them here is sufficient.
_test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
models.engine = _test_engine
models.SessionLocal = sessionmaker(bind=_test_engine)
models.Base.metadata.create_all(_test_engine)

from app import app as flask_app  # noqa: E402 - imported after engine is patched

_AUTH_HEADER = {
    "Authorization": "Basic " + base64.b64encode(b"admin:testpass").decode()
}


# ── core fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def auth():
    """HTTP Basic Auth headers for every request."""
    return _AUTH_HEADER


@pytest.fixture
def db():
    """SQLAlchemy session for direct DB access within a test."""
    session = models.get_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_db():
    """Delete all rows after each test. Runs last (yield = after test body)."""
    yield
    with _test_engine.begin() as conn:
        for table in reversed(models.Base.metadata.sorted_tables):
            conn.execute(table.delete())


# ── factory fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def make_contact(db):
    def _make(**kwargs):
        kwargs.setdefault("name", "Alice Smith")
        kwargs.setdefault("warmth", "cold")
        kwargs.setdefault("category", "other")
        c = models.Contact(**kwargs)
        db.add(c)
        db.commit()
        db.refresh(c)
        return c
    return _make


@pytest.fixture
def make_funder(db):
    def _make(**kwargs):
        kwargs.setdefault("organization", "Test Foundation")
        kwargs.setdefault("status", "research")
        f = models.Funder(**kwargs)
        db.add(f)
        db.commit()
        db.refresh(f)
        return f
    return _make


@pytest.fixture
def make_task(db):
    def _make(**kwargs):
        kwargs.setdefault("title", "Test Task")
        kwargs.setdefault("priority", "medium")
        kwargs.setdefault("status", "pending")
        t = models.Task(**kwargs)
        db.add(t)
        db.commit()
        db.refresh(t)
        return t
    return _make


@pytest.fixture
def make_interaction(db):
    def _make(**kwargs):
        kwargs.setdefault("date", date.today())
        kwargs.setdefault("type", "meeting")
        kwargs.setdefault("notes", "Test notes")
        i = models.Interaction(**kwargs)
        db.add(i)
        db.commit()
        db.refresh(i)
        return i
    return _make
