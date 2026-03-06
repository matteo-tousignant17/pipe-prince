import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import settings
from database.models import LoadORM, ValidationRecordORM
from database.session import get_db
from models.load import Load, LoadStatus
from services import dispatcher, notifier, pdf_factory
from services.parser import store_correction_example

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/slack", tags=["slack"])


def _verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str) -> None:
    """Validate Slack's HMAC-SHA256 request signature."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Invalid timestamp")

    if abs(time.time() - ts) > 300:
        raise HTTPException(status_code=403, detail="Request timestamp too old")

    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    expected = "v0=" + hmac.new(
        signing_secret.encode(), base, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")


def _load_from_db(load_id: str, db: Session) -> LoadORM:
    row = db.query(LoadORM).filter_by(load_id=load_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Load {load_id} not found")
    return row


def _orm_to_load(row: LoadORM) -> Load:
    from models.load import CargoSpec, LogisticsInfo, RouteInfo
    from datetime import datetime
    eta_raw = row.logistics_json.get("eta")
    if isinstance(eta_raw, str):
        eta = datetime.fromisoformat(eta_raw.replace("Z", "+00:00"))
    else:
        eta = datetime.now(timezone.utc)

    return Load(
        load_id=row.load_id,
        status=LoadStatus(row.status),
        cargo=CargoSpec(**row.cargo_json),
        logistics=LogisticsInfo(
            broker_name=row.logistics_json["broker_name"],
            quoted_rate=row.logistics_json["quoted_rate"],
            currency=row.logistics_json.get("currency", "USD"),
            eta=eta,
        ),
        route=RouteInfo(**row.route_json),
        cost_per_mile=row.cost_per_mile,
        rate_anomaly=row.rate_anomaly,
        rate_anomaly_pct=row.rate_anomaly_pct,
        raw_email_body=row.raw_email_body,
        broker_email=row.broker_email,
        created_at=row.created_at or datetime.now(timezone.utc),
    )


def _record_validation(
    load_id: str,
    slack_user: str,
    action: str,
    original_load: dict,
    corrected_load: Optional[dict],
    note: Optional[str],
    db: Session,
) -> None:
    record = ValidationRecordORM(
        load_id=load_id,
        validator_slack_user=slack_user,
        action=action,
        original_load=original_load,
        corrected_load=corrected_load,
        note=note,
        validated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()


async def _handle_approve(
    load_id: str,
    slack_user_id: str,
    channel: str,
    message_ts: str,
    db: Session,
) -> None:
    row = _load_from_db(load_id, db)
    load = _orm_to_load(row)

    row.status = LoadStatus.APPROVED.value
    _record_validation(
        load_id, slack_user_id, "approve",
        original_load=load.model_dump(mode="json"),
        corrected_load=None, note=None, db=db,
    )
    db.commit()

    dispatcher.dispatch_load_documents(
        load,
        row.rate_con_pdf_path or "",
        row.release_doc_pdf_path or "",
    )

    if message_ts and channel:
        notifier.update_card_to_approved(channel, message_ts, load_id)

    logger.info("Load %s approved by %s", load_id, slack_user_id)


async def _handle_reject(
    load_id: str,
    slack_user_id: str,
    channel: str,
    message_ts: str,
    note: str,
    db: Session,
) -> None:
    row = _load_from_db(load_id, db)
    load = _orm_to_load(row)

    row.status = LoadStatus.REJECTED.value
    _record_validation(
        load_id, slack_user_id, "reject",
        original_load=load.model_dump(mode="json"),
        corrected_load=None, note=note, db=db,
    )
    db.commit()

    notifier.send_panic_alert(load_id, rejected_by=slack_user_id, note=note)

    if message_ts and channel:
        notifier.update_card_to_rejected(channel, message_ts, load_id)

    logger.warning("Load %s rejected by %s — panic alert sent", load_id, slack_user_id)


async def _handle_edit(
    load_id: str,
    slack_user_id: str,
    corrected_fields: dict,
    channel: str,
    message_ts: str,
    db: Session,
) -> None:
    row = _load_from_db(load_id, db)
    original_load = _orm_to_load(row)
    original_dict = original_load.model_dump(mode="json")

    # Apply corrections to ORM JSON columns
    if "cargo" in corrected_fields:
        row.cargo_json = {**row.cargo_json, **corrected_fields["cargo"]}
    if "logistics" in corrected_fields:
        row.logistics_json = {**row.logistics_json, **corrected_fields["logistics"]}
    if "route" in corrected_fields:
        row.route_json = {**row.route_json, **corrected_fields["route"]}

    corrected_load = _orm_to_load(row)
    corrected_dict = corrected_load.model_dump(mode="json")

    _record_validation(
        load_id, slack_user_id, "edit",
        original_load=original_dict,
        corrected_load=corrected_dict,
        note="Fields edited via Slack",
        db=db,
    )

    # Feed correction back into the LLM prompt for this broker
    store_correction_example(
        broker_email=row.broker_email,
        original_load=original_dict,
        corrected_load=corrected_dict,
        db=db,
    )

    # Re-generate PDFs with corrected data
    try:
        rate_con_path = pdf_factory.generate_rate_confirmation(corrected_load)
        release_doc_path = pdf_factory.generate_release_document(corrected_load)
        row.rate_con_pdf_path = rate_con_path
        row.release_doc_pdf_path = release_doc_path
    except pdf_factory.PDFGenerationError as exc:
        logger.error("PDF re-generation failed for %s: %s", load_id, exc)

    db.commit()

    # Re-post the approval card with corrected data — does NOT auto-approve
    try:
        new_ts = notifier.send_load_approval_card(
            corrected_load,
            row.rate_con_pdf_path,
            row.release_doc_pdf_path,
        )
        row.slack_message_ts = new_ts
        db.commit()
    except notifier.NotifierError as exc:
        logger.error("Failed to re-post edited card: %s", exc)

    logger.info("Load %s edited by %s, re-posted for approval", load_id, slack_user_id)


@router.post("/actions")
async def handle_slack_action(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """
    Receives Slack interactive action payloads (URL-encoded form with 'payload' key).
    Verifies signature, routes to appropriate handler.
    Returns {"ok": True} quickly; Slack requires response within 3 seconds.
    """
    body = await request.body()

    # Verify Slack signature
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    _verify_slack_signature(settings.SLACK_SIGNING_SECRET, timestamp, body, signature)

    # Parse URL-encoded form body
    form_data = parse_qs(body.decode("utf-8"))
    payload_str = form_data.get("payload", ["{}"])[0]
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid payload JSON")

    actions = payload.get("actions", [])
    if not actions:
        return {"ok": True}

    action = actions[0]
    action_id = action.get("action_id")
    load_id = action.get("value")
    slack_user_id = payload.get("user", {}).get("id", "unknown")
    channel = payload.get("channel", {}).get("id", "")
    message_ts = payload.get("message", {}).get("ts", "")

    if action_id == "approve_load":
        await _handle_approve(load_id, slack_user_id, channel, message_ts, db)

    elif action_id == "kill_load":
        note = action.get("value", "")
        await _handle_reject(load_id, slack_user_id, channel, message_ts, note, db)

    elif action_id == "edit_load":
        # For PoC: corrected_fields come from state_values (Slack modal submission)
        # or from a simplified view_submission payload.
        state_values = payload.get("state", {}).get("values", {})
        corrected_fields = _extract_corrected_fields(state_values)
        await _handle_edit(load_id, slack_user_id, corrected_fields, channel, message_ts, db)

    else:
        logger.warning("Unknown action_id: %s", action_id)

    return {"ok": True}


def _extract_corrected_fields(state_values: dict) -> dict:
    """
    Extracts corrected cargo/logistics/route fields from Slack modal state_values.
    Keys expected match block_ids set in an edit modal (future feature).
    Returns a partial dict safe to merge into ORM JSON columns.
    """
    corrected: dict = {}

    # Cargo corrections
    cargo_changes = {}
    if qty := _get_state_value(state_values, "cargo_quantity"):
        try:
            cargo_changes["quantity"] = int(qty)
        except ValueError:
            pass
    if specs := _get_state_value(state_values, "cargo_specs"):
        cargo_changes["specs"] = specs
    if cargo_changes:
        corrected["cargo"] = cargo_changes

    # Logistics corrections
    logistics_changes = {}
    if rate := _get_state_value(state_values, "quoted_rate"):
        try:
            logistics_changes["quoted_rate"] = float(rate)
        except ValueError:
            pass
    if logistics_changes:
        corrected["logistics"] = logistics_changes

    # Route corrections
    route_changes = {}
    if miles := _get_state_value(state_values, "total_miles"):
        try:
            route_changes["total_miles"] = int(miles)
        except ValueError:
            pass
    if route_changes:
        corrected["route"] = route_changes

    return corrected


def _get_state_value(state_values: dict, block_id: str) -> Optional[str]:
    block = state_values.get(block_id, {})
    for action_id, action_data in block.items():
        value = action_data.get("value") or action_data.get("selected_option", {}).get("value")
        if value:
            return str(value)
    return None
