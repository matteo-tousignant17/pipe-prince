import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os

from config import settings
from models.load import Load

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    pass


def dispatch_load_documents(
    load: Load,
    rate_con_pdf_path: str,
    release_doc_pdf_path: str,
) -> None:
    """
    Sends Rate Con to broker and Release Doc to yard manager.
    In SHADOW_MODE, logs the intended action and returns without sending.
    """
    if settings.SHADOW_MODE:
        logger.info(
            "SHADOW MODE: would dispatch load=%s — Rate Con to broker=%s, Release Doc to yard=%s",
            load.load_id,
            load.broker_email,
            settings.YARD_MANAGER_EMAIL,
        )
        return

    _send_broker_email(load, rate_con_pdf_path)
    _send_yard_email(load, release_doc_pdf_path)
    logger.info("Dispatched documents for load %s", load.load_id)


def _build_smtp_connection() -> smtplib.SMTP:
    smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
    smtp.starttls()
    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
    return smtp


def _attach_pdf(msg: MIMEMultipart, pdf_path: str, filename: str) -> None:
    if not os.path.isfile(pdf_path):
        logger.warning("PDF not found, skipping attachment: %s", pdf_path)
        return
    with open(pdf_path, "rb") as f:
        part = MIMEApplication(f.read(), _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)


def _send_broker_email(load: Load, rate_con_pdf_path: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = settings.FROM_EMAIL
    msg["To"] = load.broker_email
    msg["Subject"] = f"Rate Confirmation — Load {load.load_id}"

    body = (
        f"Hi {load.logistics.broker_name},\n\n"
        f"Please find the Rate Confirmation for Load {load.load_id} attached.\n\n"
        f"Load Details:\n"
        f"  Cargo: {load.cargo.quantity} {load.cargo.unit} — {load.cargo.specs}\n"
        f"  Route: {load.route.origin} → {load.route.destination} ({load.route.total_miles} miles)\n"
        f"  Agreed Rate: ${load.logistics.quoted_rate:,.2f} {load.logistics.currency}\n"
        f"  ETA: {load.logistics.eta.strftime('%Y-%m-%d')}\n\n"
        f"Please sign and return at your earliest convenience.\n\n"
        f"Regards,\nPipe Prince Operations"
    )
    msg.attach(MIMEText(body, "plain"))
    _attach_pdf(msg, rate_con_pdf_path, f"{load.load_id}_rate_con.pdf")

    try:
        with _build_smtp_connection() as smtp:
            smtp.send_message(msg)
        logger.info("Sent Rate Con for load %s to %s", load.load_id, load.broker_email)
    except smtplib.SMTPException as exc:
        raise DispatchError(f"SMTP error sending to broker: {exc}") from exc


def _send_yard_email(load: Load, release_doc_pdf_path: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = settings.FROM_EMAIL
    msg["To"] = settings.YARD_MANAGER_EMAIL
    msg["Subject"] = f"Release Authorization — Load {load.load_id}"

    body = (
        f"Hi Yard Manager,\n\n"
        f"Please find the Release Document for Load {load.load_id} attached.\n\n"
        f"Release Details:\n"
        f"  Cargo: {load.cargo.quantity} {load.cargo.unit} — {load.cargo.specs}\n"
        f"  Release From: {load.route.origin}\n"
        f"  Delivering To: {load.route.destination}\n"
        f"  Broker: {load.logistics.broker_name} ({load.broker_email})\n"
        f"  Expected Pickup: {load.logistics.eta.strftime('%Y-%m-%d')}\n\n"
        f"Please verify cargo count before release and retain this document for 90 days.\n\n"
        f"Regards,\nPipe Prince Operations"
    )
    msg.attach(MIMEText(body, "plain"))
    _attach_pdf(msg, release_doc_pdf_path, f"{load.load_id}_release_doc.pdf")

    try:
        with _build_smtp_connection() as smtp:
            smtp.send_message(msg)
        logger.info(
            "Sent Release Doc for load %s to %s", load.load_id, settings.YARD_MANAGER_EMAIL
        )
    except smtplib.SMTPException as exc:
        raise DispatchError(f"SMTP error sending to yard: {exc}") from exc
