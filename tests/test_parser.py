import json
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from database.models import BrokerProfileORM, LoadORM
from models.load import LoadStatus
from services.parser import (
    ParserError,
    _calculate_cost_per_mile,
    _check_rate_anomaly,
    _generate_load_id,
    parse_email_to_load,
    store_correction_example,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

VALID_CLAUDE_RESPONSE = json.dumps({
    "cargo": {
        "type": "structural_tubing",
        "quantity": 230,
        "unit": "joints",
        "specs": "2 7/8 6.5# range 2",
    },
    "logistics": {
        "broker_name": "DSV Brokerage",
        "quoted_rate": 950.00,
        "currency": "USD",
        "eta": "2026-03-10T09:00:00Z",
    },
    "route": {
        "origin": "Oklahoma City, OK",
        "destination": "Dallas, TX",
        "total_miles": 205,
    },
})


def _mock_claude(response_text: str):
    """Returns a mock that mimics anthropic client.messages.create."""
    content_block = MagicMock()
    content_block.text = response_text
    message = MagicMock()
    message.content = [content_block]
    mock_create = MagicMock(return_value=message)
    return mock_create


# ── Unit tests ────────────────────────────────────────────────────────────────

def test_calculate_cost_per_mile_basic():
    assert _calculate_cost_per_mile(950.0, 205) == pytest.approx(4.6341, rel=1e-3)


def test_calculate_cost_per_mile_zero_miles():
    assert _calculate_cost_per_mile(950.0, 0) is None


def test_generate_load_id_sequential(db_session):
    id1 = _generate_load_id(db_session)
    assert id1.startswith("LS-2026-")
    # Insert a dummy row to simulate an existing load
    db_session.add(
        LoadORM(
            load_id=id1,
            status="pending_approval",
            cargo_json={},
            logistics_json={"quoted_rate": 900},
            route_json={},
            raw_email_body="",
            broker_email="test@example.com",
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    id2 = _generate_load_id(db_session)
    assert id2 != id1
    assert int(id2.split("-")[-1]) == int(id1.split("-")[-1]) + 1


def test_check_rate_anomaly_not_enough_history(db_session):
    is_anomaly, pct = _check_rate_anomaly(950.0, "new@broker.com", db_session)
    assert is_anomaly is False
    assert pct == 0.0


def test_check_rate_anomaly_flagged(db_session):
    """Seed 3 loads at $800, then check $950 — should flag as anomaly (>10% above avg)."""
    from datetime import timedelta
    base = datetime.now(timezone.utc) - timedelta(days=5)
    for i in range(3):
        db_session.add(
            LoadORM(
                load_id=f"LS-TEST-A{i}",
                status="approved",
                cargo_json={},
                logistics_json={"quoted_rate": 800.0},
                route_json={},
                raw_email_body="",
                broker_email="anomaly@broker.com",
                created_at=base,
            )
        )
    db_session.flush()

    is_anomaly, pct = _check_rate_anomaly(950.0, "anomaly@broker.com", db_session)
    assert is_anomaly is True
    assert pct == pytest.approx(0.1875, rel=1e-3)


def test_check_rate_anomaly_not_flagged(db_session):
    """$810 against avg $800 — within 10%, should not flag."""
    from datetime import timedelta
    base = datetime.now(timezone.utc) - timedelta(days=5)
    for i in range(3):
        db_session.add(
            LoadORM(
                load_id=f"LS-TEST-B{i}",
                status="approved",
                cargo_json={},
                logistics_json={"quoted_rate": 800.0},
                route_json={},
                raw_email_body="",
                broker_email="normal@broker.com",
                created_at=base,
            )
        )
    db_session.flush()

    is_anomaly, pct = _check_rate_anomaly(810.0, "normal@broker.com", db_session)
    assert is_anomaly is False


# ── Integration tests (Claude mocked) ─────────────────────────────────────────

def test_parse_well_formed_email_creates_load(db_session):
    with open(os.path.join(FIXTURES, "broker_email_1.txt")) as f:
        body = f.read()

    with patch("services.parser.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create = _mock_claude(VALID_CLAUDE_RESPONSE)
        load = parse_email_to_load(
            email_body=body,
            broker_email="john@dsvbrokerage.com",
            broker_name_hint="John Smith",
            db=db_session,
        )

    assert load.load_id.startswith("LS-2026-")
    assert load.cargo.quantity == 230
    assert load.logistics.quoted_rate == 950.0
    assert load.route.total_miles == 205
    assert load.cost_per_mile == pytest.approx(4.6341, rel=1e-3)
    assert load.status == LoadStatus.PENDING_APPROVAL


def test_parse_creates_broker_profile(db_session):
    """First email from a new broker auto-creates a BrokerProfileORM."""
    with patch("services.parser.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create = _mock_claude(VALID_CLAUDE_RESPONSE)
        parse_email_to_load(
            email_body="test email",
            broker_email="newbroker@example.com",
            broker_name_hint="New Broker Co",
            db=db_session,
        )

    profile = db_session.query(BrokerProfileORM).filter_by(
        broker_email="newbroker@example.com"
    ).first()
    assert profile is not None
    assert profile.broker_name == "New Broker Co"
    assert profile.load_count == 1


def test_parse_increments_broker_load_count(db_session):
    with patch("services.parser.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create = _mock_claude(VALID_CLAUDE_RESPONSE)
        parse_email_to_load("email 1", "counter@broker.com", "Counter Broker", db_session)
        mock_cls.return_value.messages.create = _mock_claude(VALID_CLAUDE_RESPONSE)
        parse_email_to_load("email 2", "counter@broker.com", "Counter Broker", db_session)

    profile = db_session.query(BrokerProfileORM).filter_by(
        broker_email="counter@broker.com"
    ).first()
    assert profile.load_count == 2


def test_parse_invalid_json_raises_parser_error(db_session):
    with patch("services.parser.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create = _mock_claude("this is not json")
        with pytest.raises(ParserError, match="invalid JSON"):
            parse_email_to_load("email", "bad@broker.com", "Bad Broker", db_session)


def test_parse_null_eta_defaults(db_session):
    """If Claude returns null for eta, parser should supply a default."""
    data = json.loads(VALID_CLAUDE_RESPONSE)
    data["logistics"]["eta"] = None
    with patch("services.parser.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create = _mock_claude(json.dumps(data))
        load = parse_email_to_load("email", "eta@broker.com", "ETA Broker", db_session)
    assert load.logistics.eta is not None


def test_correction_examples_injected_in_prompt(db_session):
    """Verify correction_examples appear in the built prompt."""
    profile = BrokerProfileORM(
        broker_email="feedback@broker.com",
        broker_name="Feedback Broker",
        first_seen=datetime.now(timezone.utc),
        load_count=2,
        correction_examples=[
            {
                "original": {"cargo": {"quantity": 100}},
                "corrected": {"cargo": {"quantity": 150}},
            }
        ],
    )
    db_session.add(profile)
    db_session.flush()

    captured_prompt = {}

    def capture_create(**kwargs):
        captured_prompt["messages"] = kwargs.get("messages", [])
        content_block = MagicMock()
        content_block.text = VALID_CLAUDE_RESPONSE
        msg = MagicMock()
        msg.content = [content_block]
        return msg

    with patch("services.parser.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create = capture_create
        parse_email_to_load("some email", "feedback@broker.com", "Feedback Broker", db_session)

    user_content = captured_prompt["messages"][0]["content"]
    assert "Previous corrections" in user_content
    assert "quantity" in user_content


def test_store_correction_example_trims_to_five(db_session):
    profile = BrokerProfileORM(
        broker_email="trim@broker.com",
        broker_name="Trim Broker",
        first_seen=datetime.now(timezone.utc),
        load_count=1,
        correction_examples=[{"original": {}, "corrected": {}}] * 5,
    )
    db_session.add(profile)
    db_session.flush()

    store_correction_example(
        "trim@broker.com",
        {"original": "new"},
        {"corrected": "new"},
        db_session,
    )

    profile = db_session.query(BrokerProfileORM).filter_by(
        broker_email="trim@broker.com"
    ).first()
    assert len(profile.correction_examples) == 5
