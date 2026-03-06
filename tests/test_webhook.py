import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

VALID_CLAUDE_RESPONSE = json.dumps({
    "cargo": {"type": "structural_tubing", "quantity": 230, "unit": "joints", "specs": "2 7/8 6.5# range 2"},
    "logistics": {"broker_name": "DSV Brokerage", "quoted_rate": 950.00, "currency": "USD", "eta": "2026-03-10T09:00:00Z"},
    "route": {"origin": "Oklahoma City, OK", "destination": "Dallas, TX", "total_miles": 205},
})


def _claude_mock(response_text: str):
    content_block = MagicMock()
    content_block.text = response_text
    message = MagicMock()
    message.content = [content_block]
    return MagicMock(return_value=message)


def test_webhook_returns_received(client, sample_postmark_payload):
    """Webhook returns 200 + 'received' status immediately."""
    with patch("routes.webhook._process_email_async", new_callable=AsyncMock):
        response = client.post(
            "/webhook/email",
            json=sample_postmark_payload,
            headers={"x-postmark-signature": "test-webhook-token"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "received"
    assert data["message_id"] == "test-msg-001"


def test_webhook_rejects_missing_token(client, sample_postmark_payload):
    """No token header → 403."""
    response = client.post("/webhook/email", json=sample_postmark_payload)
    assert response.status_code == 403


def test_webhook_rejects_wrong_token(client, sample_postmark_payload):
    """Wrong token → 403."""
    response = client.post(
        "/webhook/email",
        json=sample_postmark_payload,
        headers={"x-postmark-signature": "wrong-token"},
    )
    assert response.status_code == 403


def test_webhook_rejects_empty_body(client):
    payload = {
        "From": "john@dsvbrokerage.com",
        "FromFull": {"Email": "john@dsvbrokerage.com", "Name": "John"},
        "Subject": "Test",
        "TextBody": "   ",
        "HtmlBody": "",
        "MessageID": "empty-001",
        "Date": "2026-03-06T12:00:00Z",
    }
    response = client.post(
        "/webhook/email",
        json=payload,
        headers={"x-postmark-signature": "test-webhook-token"},
    )
    assert response.status_code == 400


def test_webhook_full_flow_persists_load(client, db_session, tmp_path):
    """End-to-end: email in → LoadORM row created in DB."""
    from database.models import LoadORM

    with open(os.path.join(FIXTURES, "broker_email_1.txt")) as f:
        body = f.read()

    payload = {
        "From": "John Smith <john@dsvbrokerage.com>",
        "FromFull": {"Email": "john@fullflow@dsvbrokerage.com", "Name": "Full Flow Test"},
        "Subject": "Full flow test",
        "TextBody": body,
        "HtmlBody": "",
        "MessageID": "full-flow-001",
        "Date": "2026-03-06T12:00:00Z",
    }

    with patch("services.parser.anthropic.Anthropic") as mock_cls, \
         patch("services.pdf_factory.PDF_OUTPUT_DIR", str(tmp_path)), \
         patch("services.notifier.WebClient") as mock_slack:
        mock_cls.return_value.messages.create = _claude_mock(VALID_CLAUDE_RESPONSE)
        mock_slack.return_value.chat_postMessage.return_value = {"ts": "12345.67890"}
        mock_slack.return_value.files_upload_v2.return_value = {"ok": True}

        # TestClient runs background tasks synchronously
        response = client.post(
            "/webhook/email",
            json=payload,
            headers={"x-postmark-signature": "test-webhook-token"},
        )

    assert response.status_code == 200
    # Filter by the unique broker email used in this test to avoid cross-test contamination
    load_row = (
        db_session.query(LoadORM)
        .filter_by(broker_email="john@fullflow@dsvbrokerage.com")
        .first()
    )
    assert load_row is not None
    assert load_row.load_id.startswith("LS-2026-")
    assert load_row.status == "pending_approval"


def test_webhook_uses_html_body_fallback(client, db_session, tmp_path):
    """When TextBody is empty, fall back to stripping HtmlBody."""
    with patch("routes.webhook._process_email_async", new_callable=AsyncMock) as mock_proc:
        response = client.post(
            "/webhook/email",
            json={
                "From": "broker@example.com",
                "FromFull": {"Email": "broker@example.com", "Name": "Broker"},
                "Subject": "HTML email",
                "TextBody": "",
                "HtmlBody": "<p>230 joints 2 7/8 from OKC to Dallas rate $950</p>",
                "MessageID": "html-001",
                "Date": "2026-03-06T12:00:00Z",
            },
            headers={"x-postmark-signature": "test-webhook-token"},
        )
    assert response.status_code == 200
    call_kwargs = mock_proc.call_args.kwargs
    assert "230 joints" in call_kwargs["email_body"]
