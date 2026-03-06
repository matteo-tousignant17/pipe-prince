import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, StaticPool
from sqlalchemy.orm import sessionmaker

# Force shadow mode before any imports that touch settings
os.environ["SHADOW_MODE"] = "true"
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
os.environ["SLACK_SIGNING_SECRET"] = "test-signing-secret"
os.environ["POSTMARK_WEBHOOK_TOKEN"] = "test-webhook-token"

from database.models import Base
from database.session import get_db
from main import app

# StaticPool ensures all connections share the same in-memory SQLite database,
# which is critical for background tasks that open new connections.
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_engine():
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    TestingSessionLocal = sessionmaker(bind=test_engine)
    session = TestingSessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_postmark_payload():
    fixture_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "broker_email_1.txt"
    )
    with open(fixture_path) as f:
        email_body = f.read()
    return {
        "From": "John Smith <john@dsvbrokerage.com>",
        "FromFull": {"Email": "john@dsvbrokerage.com", "Name": "John Smith"},
        "Subject": "Load Quote - OKC to Dallas",
        "TextBody": email_body,
        "HtmlBody": "",
        "MessageID": "test-msg-001",
        "Date": "2026-03-06T12:00:00Z",
    }


@pytest.fixture
def make_test_load():
    """Factory for a minimal valid Load object."""
    from datetime import datetime, timezone
    from models.load import Load, CargoSpec, LogisticsInfo, RouteInfo, LoadStatus

    def _make(**overrides):
        defaults = dict(
            load_id="LS-2026-001",
            status=LoadStatus.PENDING_APPROVAL,
            cargo=CargoSpec(
                type="structural_tubing",
                quantity=230,
                unit="joints",
                specs="2 7/8 6.5# range 2",
            ),
            logistics=LogisticsInfo(
                broker_name="DSV Brokerage",
                quoted_rate=950.00,
                currency="USD",
                eta=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
            ),
            route=RouteInfo(
                origin="Oklahoma City, OK",
                destination="Dallas, TX",
                total_miles=205,
            ),
            cost_per_mile=4.63,
            rate_anomaly=False,
            rate_anomaly_pct=0.0,
            raw_email_body="test email body",
            broker_email="john@dsvbrokerage.com",
            created_at=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return Load(**defaults)

    return _make
