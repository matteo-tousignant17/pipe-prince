import logging
import os
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import settings
from models.load import Load

logger = logging.getLogger(__name__)


class NotifierError(Exception):
    pass


def _get_client() -> WebClient:
    return WebClient(token=settings.SLACK_BOT_TOKEN)


def _format_cargo_line(load: Load) -> str:
    return f"{load.cargo.quantity} {load.cargo.unit.title()} ({load.cargo.specs})"


def _format_rate_line(load: Load) -> str:
    cpm = f"${load.cost_per_mile:.2f}/mile" if load.cost_per_mile else "N/A"
    line = f"${load.logistics.quoted_rate:,.2f} ({cpm})"
    if load.rate_anomaly:
        pct = round((load.rate_anomaly_pct or 0) * 100, 1)
        line += f"  ⚠️ {pct}% above 30-day avg"
    return line


def _build_approval_blocks(load: Load) -> list[dict]:
    eta_str = load.logistics.eta.strftime("%Y-%m-%d")
    shadow_note = ""
    if settings.SHADOW_MODE:
        shadow_note = "\n🔕 *SHADOW MODE* — documents will NOT be dispatched on approve."

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚛 Load {load.load_id} — Pending Approval",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Vendor:*\n{load.logistics.broker_name}"},
                {"type": "mrkdwn", "text": f"*Load:*\n{_format_cargo_line(load)}"},
                {"type": "mrkdwn", "text": f"*Rate:*\n{_format_rate_line(load)}"},
                {"type": "mrkdwn", "text": f"*Route:*\n{load.route.origin} → {load.route.destination} ({load.route.total_miles} mi)"},
                {"type": "mrkdwn", "text": f"*ETA:*\n{eta_str}"},
                {"type": "mrkdwn", "text": f"*Broker Email:*\n{load.broker_email}"},
            ],
        },
    ]

    if shadow_note:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": shadow_note}],
        })

    blocks.append({
        "type": "actions",
        "block_id": f"load_actions_{load.load_id}",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ Approve & Send", "emoji": True},
                "style": "primary",
                "action_id": "approve_load",
                "value": load.load_id,
                "confirm": {
                    "title": {"type": "plain_text", "text": "Confirm Approval"},
                    "text": {"type": "mrkdwn", "text": f"Send Rate Con to *{load.logistics.broker_name}* and Release Doc to yard?"},
                    "confirm": {"type": "plain_text", "text": "Yes, send it"},
                    "deny": {"type": "plain_text", "text": "Cancel"},
                },
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✏️ Edit Details", "emoji": True},
                "action_id": "edit_load",
                "value": load.load_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "❌ Kill Load", "emoji": True},
                "style": "danger",
                "action_id": "kill_load",
                "value": load.load_id,
                "confirm": {
                    "title": {"type": "plain_text", "text": "Kill this load?"},
                    "text": {"type": "mrkdwn", "text": "This will reject the load and alert Matteo immediately."},
                    "confirm": {"type": "plain_text", "text": "Kill it"},
                    "deny": {"type": "plain_text", "text": "Cancel"},
                },
            },
        ],
    })

    blocks.append({"type": "divider"})
    return blocks


def send_load_approval_card(
    load: Load,
    rate_con_pdf_path: Optional[str] = None,
    release_doc_pdf_path: Optional[str] = None,
) -> str:
    """
    Posts an interactive approval card to SLACK_CHANNEL_ID.
    Uploads PDFs as attachments if paths are provided.
    Returns Slack message timestamp (ts).
    """
    client = _get_client()
    blocks = _build_approval_blocks(load)

    try:
        # Upload PDFs first
        for pdf_path, title in [
            (rate_con_pdf_path, f"Rate Con — {load.load_id}"),
            (release_doc_pdf_path, f"Release Doc — {load.load_id}"),
        ]:
            if pdf_path and os.path.isfile(pdf_path):
                try:
                    with open(pdf_path, "rb") as f:
                        client.files_upload_v2(
                            channel=settings.SLACK_CHANNEL_ID,
                            file=f,
                            filename=os.path.basename(pdf_path),
                            title=title,
                        )
                except SlackApiError as exc:
                    logger.warning("PDF upload failed (%s): %s", title, exc)

        response = client.chat_postMessage(
            channel=settings.SLACK_CHANNEL_ID,
            text=f"Load {load.load_id} pending approval",
            blocks=blocks,
        )
        ts = response["ts"]
        logger.info("Posted approval card for %s (ts=%s)", load.load_id, ts)
        return ts

    except SlackApiError as exc:
        raise NotifierError(f"Slack API error: {exc}") from exc


def send_panic_alert(load_id: str, rejected_by: str, note: str) -> None:
    """DM Matteo when Pat kills a load."""
    client = _get_client()
    text = (
        f"🚨 *Validation Failure — Load {load_id}*\n"
        f"Rejected by: <@{rejected_by}>\n"
        f"Note: {note or 'No note provided'}\n\n"
        f"Please handle this load manually."
    )
    try:
        client.chat_postMessage(
            channel=settings.SLACK_MATTEO_USER_ID,
            text=text,
        )
        logger.info("Panic alert sent to Matteo for load %s", load_id)
    except SlackApiError as exc:
        logger.error("Failed to send panic alert: %s", exc)


def update_card_to_approved(channel: str, ts: str, load_id: str) -> None:
    client = _get_client()
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"✅ *Load {load_id} — APPROVED*\nDocuments dispatched to broker and yard.",
            },
        }
    ]
    try:
        client.chat_update(channel=channel, ts=ts, text=f"Load {load_id} approved", blocks=blocks)
    except SlackApiError as exc:
        logger.error("Failed to update card to approved: %s", exc)


def update_card_to_rejected(channel: str, ts: str, load_id: str) -> None:
    client = _get_client()
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"❌ *Load {load_id} — REJECTED*\nMatteo has been alerted.",
            },
        }
    ]
    try:
        client.chat_update(channel=channel, ts=ts, text=f"Load {load_id} rejected", blocks=blocks)
    except SlackApiError as exc:
        logger.error("Failed to update card to rejected: %s", exc)
