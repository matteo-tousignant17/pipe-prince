import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from database.models import LoadORM, ValidationRecordORM
from models.load import LoadStatus


def _build_slack_payload(action_id: str, load_id: str, user_id: str = "U_PAT") -> dict:
    return {
        "type": "block_actions",
        "user": {"id": user_id},
        "channel": {"id": "C_LOADS"},
        "message": {"ts": "1234567890.123456"},
        "actions": [
            {"action_id": action_id, "value": load_id}
        ],
        "state": {"values": {}},
    }


def _sign_body(raw_body: str, signing_secret: str = "test-signing-secret") -> dict:
    """Compute valid Slack signature headers over the full URL-encoded body."""
    ts = str(int(time.time()))
    base = f"v0:{ts}:{raw_body}".encode()
    sig = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _post_slack_action(client, payload_dict: dict) -> object:
    payload_str = json.dumps(payload_dict)
    # Slack sends: payload=<url-encoded-json>
    # The signature is computed over the full raw body string
    raw_body = f"payload={payload_str}"
    headers = _sign_body(raw_body)
    return client.post("/slack/actions", content=raw_body.encode(), headers=headers)


def _seed_load(db_session, load_id: str = "LS-2026-SLACKTEST", status: str = "pending_approval") -> LoadORM:
    row = LoadORM(
        load_id=load_id,
        status=status,
        cargo_json={"type": "structural_tubing", "quantity": 230, "unit": "joints", "specs": "2 7/8 6.5# range 2"},
        logistics_json={
            "broker_name": "Test Broker",
            "quoted_rate": 950.0,
            "currency": "USD",
            "eta": "2026-03-10T09:00:00+00:00",
        },
        route_json={"origin": "Oklahoma City, OK", "destination": "Dallas, TX", "total_miles": 205},
        cost_per_mile=4.63,
        rate_anomaly=False,
        rate_anomaly_pct=0.0,
        raw_email_body="test body",
        broker_email="test@broker.com",
        created_at=datetime.now(timezone.utc),
        slack_message_ts="1234567890.123456",
        slack_channel_id="C_LOADS",
    )
    db_session.add(row)
    db_session.commit()
    return row


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_slack_action_invalid_signature(client):
    raw_body = "payload={}"
    response = client.post(
        "/slack/actions",
        content=raw_body.encode(),
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=badsignature",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert response.status_code == 403


def test_approve_action_sets_status_approved(client, db_session):
    row = _seed_load(db_session, "LS-2026-APPROVE")

    with patch("services.dispatcher.dispatch_load_documents"), \
         patch("services.notifier.update_card_to_approved"):
        resp = _post_slack_action(
            client, _build_slack_payload("approve_load", "LS-2026-APPROVE")
        )

    assert resp.status_code == 200
    db_session.refresh(row)
    assert row.status == LoadStatus.APPROVED.value


def test_approve_action_creates_validation_record(client, db_session):
    _seed_load(db_session, "LS-2026-VALIDREC")

    with patch("services.dispatcher.dispatch_load_documents"), \
         patch("services.notifier.update_card_to_approved"):
        _post_slack_action(client, _build_slack_payload("approve_load", "LS-2026-VALIDREC"))

    record = (
        db_session.query(ValidationRecordORM)
        .filter_by(load_id="LS-2026-VALIDREC")
        .first()
    )
    assert record is not None
    assert record.action == "approve"
    assert record.validator_slack_user == "U_PAT"


def test_approve_shadow_mode_does_not_dispatch(client, db_session):
    """In shadow mode, dispatcher.dispatch_load_documents should log but not call SMTP."""
    _seed_load(db_session, "LS-2026-SHADOW")

    with patch("services.dispatcher._send_broker_email") as mock_broker, \
         patch("services.dispatcher._send_yard_email") as mock_yard, \
         patch("services.notifier.update_card_to_approved"):
        _post_slack_action(client, _build_slack_payload("approve_load", "LS-2026-SHADOW"))

    # In shadow mode (SHADOW_MODE=true from conftest), send methods must NOT be called
    mock_broker.assert_not_called()
    mock_yard.assert_not_called()


def test_kill_load_sets_status_rejected(client, db_session):
    _seed_load(db_session, "LS-2026-KILL")

    with patch("services.notifier.send_panic_alert"), \
         patch("services.notifier.update_card_to_rejected"):
        resp = _post_slack_action(
            client, _build_slack_payload("kill_load", "LS-2026-KILL")
        )

    assert resp.status_code == 200
    row = db_session.query(LoadORM).filter_by(load_id="LS-2026-KILL").first()
    assert row.status == LoadStatus.REJECTED.value


def test_kill_load_triggers_panic_alert(client, db_session):
    _seed_load(db_session, "LS-2026-PANIC")

    with patch("services.notifier.send_panic_alert") as mock_panic, \
         patch("services.notifier.update_card_to_rejected"):
        _post_slack_action(client, _build_slack_payload("kill_load", "LS-2026-PANIC"))

    mock_panic.assert_called_once()
    call_kwargs = mock_panic.call_args
    assert "LS-2026-PANIC" in str(call_kwargs)


def test_edit_action_stores_correction(client, db_session):
    _seed_load(db_session, "LS-2026-EDIT")

    # Build payload with state_values simulating a corrected quantity
    payload = _build_slack_payload("edit_load", "LS-2026-EDIT")
    payload["state"]["values"] = {
        "cargo_quantity": {"cargo_quantity_input": {"value": "250"}}
    }

    with patch("services.pdf_factory.generate_rate_confirmation", return_value="/tmp/rc.pdf"), \
         patch("services.pdf_factory.generate_release_document", return_value="/tmp/rd.pdf"), \
         patch("services.notifier.send_load_approval_card", return_value="new_ts"):
        resp = _post_slack_action(client, payload)

    assert resp.status_code == 200

    # Load should have the corrected quantity in DB
    row = db_session.query(LoadORM).filter_by(load_id="LS-2026-EDIT").first()
    assert row.cargo_json["quantity"] == 250


def test_edit_does_not_auto_approve(client, db_session):
    """After edit, status should remain pending_approval — not auto-approved."""
    _seed_load(db_session, "LS-2026-EDITPEND")

    with patch("services.pdf_factory.generate_rate_confirmation", return_value="/tmp/rc.pdf"), \
         patch("services.pdf_factory.generate_release_document", return_value="/tmp/rd.pdf"), \
         patch("services.notifier.send_load_approval_card", return_value="new_ts"):
        _post_slack_action(client, _build_slack_payload("edit_load", "LS-2026-EDITPEND"))

    row = db_session.query(LoadORM).filter_by(load_id="LS-2026-EDITPEND").first()
    assert row.status == LoadStatus.PENDING_APPROVAL.value


def test_unknown_action_id_returns_ok(client, db_session):
    resp = _post_slack_action(client, _build_slack_payload("some_unknown_action", "LS-2026-UNKNOWN"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
