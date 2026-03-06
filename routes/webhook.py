import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database.models import LoadORM
from database.session import get_db
from models.load import Load
from services import notifier, parser, pdf_factory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


class FromFullSchema(BaseModel):
    Email: str
    Name: str = ""


class PostmarkEmailPayload(BaseModel):
    From: str
    FromFull: FromFullSchema
    Subject: str
    TextBody: str
    HtmlBody: str = ""
    MessageID: str
    Date: str


def _validate_webhook_token(token: Optional[str]) -> None:
    if token != settings.POSTMARK_WEBHOOK_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid webhook token")


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip()


def _persist_load(load: Load, db: Session) -> LoadORM:
    orm = LoadORM(
        load_id=load.load_id,
        status=load.status.value,
        cargo_json=load.cargo.model_dump(),
        logistics_json={
            **load.logistics.model_dump(),
            "eta": load.logistics.eta.isoformat(),
        },
        route_json=load.route.model_dump(),
        cost_per_mile=load.cost_per_mile,
        rate_anomaly=load.rate_anomaly,
        rate_anomaly_pct=load.rate_anomaly_pct,
        raw_email_body=load.raw_email_body,
        broker_email=load.broker_email,
        created_at=load.created_at,
    )
    db.add(orm)
    db.commit()
    return orm


async def _process_email_async(
    email_body: str,
    broker_email: str,
    broker_name_hint: str,
    db: Session,
) -> None:
    try:
        load = parser.parse_email_to_load(
            email_body=email_body,
            broker_email=broker_email,
            broker_name_hint=broker_name_hint,
            db=db,
        )
    except parser.ParserError as exc:
        logger.error("Parser failed for email from %s: %s", broker_email, exc)
        return

    orm = _persist_load(load, db)

    try:
        rate_con_path = pdf_factory.generate_rate_confirmation(load)
        release_doc_path = pdf_factory.generate_release_document(load)
    except pdf_factory.PDFGenerationError as exc:
        logger.error("PDF generation failed for %s: %s", load.load_id, exc)
        rate_con_path = None
        release_doc_path = None

    if rate_con_path:
        orm.rate_con_pdf_path = rate_con_path
    if release_doc_path:
        orm.release_doc_pdf_path = release_doc_path
    db.commit()

    try:
        ts = notifier.send_load_approval_card(load, rate_con_path, release_doc_path)
        orm.slack_message_ts = ts
        orm.slack_channel_id = settings.SLACK_CHANNEL_ID
        db.commit()
    except notifier.NotifierError as exc:
        logger.error("Slack notification failed for %s: %s", load.load_id, exc)


@router.post("/email")
async def receive_email(
    payload: PostmarkEmailPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_postmark_signature: Optional[str] = Header(None),
) -> dict:
    """
    Inbound email webhook from Postmark.
    Returns 200 immediately; all processing runs as a background task.
    """
    _validate_webhook_token(x_postmark_signature)

    broker_email = payload.FromFull.Email
    broker_name_hint = payload.FromFull.Name or broker_email.split("@")[0]
    email_body = payload.TextBody or _strip_html(payload.HtmlBody)

    if not email_body.strip():
        raise HTTPException(status_code=400, detail="Email body is empty")

    background_tasks.add_task(
        _process_email_async,
        email_body=email_body,
        broker_email=broker_email,
        broker_name_hint=broker_name_hint,
        db=db,
    )

    return {"status": "received", "message_id": payload.MessageID}
