import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import anthropic
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from database.models import BrokerProfileORM, LoadORM
from models.load import CargoSpec, Load, LoadStatus, LogisticsInfo, RouteInfo

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a logistics data extraction assistant for a pipe trading company.
Extract structured data from broker emails into the exact JSON schema provided.
If a field cannot be determined, use null. Never invent or fabricate data.
Return ONLY valid JSON — no markdown fences, no explanation, no extra text.

Required JSON schema:
{
  "cargo": {
    "type": "string (e.g. 'structural_tubing', 'casing', 'tubing')",
    "quantity": integer,
    "unit": "string (e.g. 'joints', 'feet', 'tons')",
    "specs": "string (raw spec string, e.g. '2 7/8 6.5# range 2')"
  },
  "logistics": {
    "broker_name": "string",
    "quoted_rate": float,
    "currency": "USD",
    "eta": "ISO 8601 datetime string or null"
  },
  "route": {
    "origin": "string (City, ST)",
    "destination": "string (City, ST)",
    "total_miles": integer or null
  }
}"""


class ParserError(Exception):
    pass


def _build_extraction_prompt(
    email_body: str,
    broker_name: str,
    correction_examples: list[dict],
) -> str:
    parts = [f"Broker name (from email header): {broker_name}\n\nEmail body:\n{email_body}"]

    if correction_examples:
        parts.append("\n\nPrevious corrections for this broker (use as reference):")
        for i, ex in enumerate(correction_examples[-3:], 1):
            parts.append(
                f"\nExample {i}:\n"
                f"  Original extraction: {json.dumps(ex.get('original', {}))}\n"
                f"  Corrected by human: {json.dumps(ex.get('corrected', {}))}"
            )

    parts.append("\n\nExtract the load data from the email above. Return only JSON.")
    return "".join(parts)


def _get_or_create_broker_profile(
    broker_email: str,
    broker_name_hint: str,
    db: Session,
) -> BrokerProfileORM:
    profile = db.query(BrokerProfileORM).filter_by(broker_email=broker_email).first()
    if profile is None:
        profile = BrokerProfileORM(
            broker_email=broker_email,
            broker_name=broker_name_hint or broker_email.split("@")[0],
            first_seen=datetime.now(timezone.utc),
            load_count=0,
            correction_examples=[],
        )
        db.add(profile)
        db.flush()
        logger.info("Created new broker profile for %s", broker_email)
    profile.load_count = (profile.load_count or 0) + 1
    db.flush()
    return profile


def _calculate_cost_per_mile(rate: float, miles: int) -> Optional[float]:
    if not miles or miles <= 0:
        return None
    return round(rate / miles, 4)


def _check_rate_anomaly(
    rate: float,
    broker_email: str,
    db: Session,
) -> tuple[bool, float]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    rows = (
        db.query(LoadORM)
        .filter(
            LoadORM.broker_email == broker_email,
            LoadORM.created_at >= cutoff,
            LoadORM.status != LoadStatus.REJECTED,
        )
        .all()
    )
    if len(rows) < 3:
        return False, 0.0

    rates = [r.logistics_json.get("quoted_rate", 0) for r in rows if r.logistics_json]
    if not rates:
        return False, 0.0

    avg = sum(rates) / len(rates)
    if avg == 0:
        return False, 0.0

    pct_above = (rate - avg) / avg
    return pct_above > 0.10, round(pct_above, 4)


def _generate_load_id(db: Session) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"LS-{year}-"
    count = (
        db.query(func.count(LoadORM.load_id))
        .filter(LoadORM.load_id.like(f"{prefix}%"))
        .scalar()
        or 0
    )
    return f"{prefix}{count + 1:03d}"


def store_correction_example(
    broker_email: str,
    original_load: dict,
    corrected_load: dict,
    db: Session,
) -> None:
    profile = db.query(BrokerProfileORM).filter_by(broker_email=broker_email).first()
    if profile is None:
        logger.warning("Cannot store correction: no profile for %s", broker_email)
        return

    examples = list(profile.correction_examples or [])
    examples.append({"original": original_load, "corrected": corrected_load})
    profile.correction_examples = examples[-5:]  # keep last 5
    db.flush()


def parse_email_to_load(
    email_body: str,
    broker_email: str,
    broker_name_hint: str,
    db: Session,
) -> Load:
    """
    Orchestrates email parsing:
    1. Get/create broker profile
    2. Build prompt with correction examples
    3. Call Claude
    4. Validate JSON into Load
    5. Compute derived fields (load_id, cost_per_mile, rate_anomaly)
    """
    profile = _get_or_create_broker_profile(broker_email, broker_name_hint, db)

    prompt = _build_extraction_prompt(
        email_body=email_body,
        broker_name=profile.broker_name,
        correction_examples=profile.correction_examples or [],
    )

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_json = message.content[0].text.strip()
    except Exception as exc:
        raise ParserError(f"Claude API call failed: {exc}") from exc

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ParserError(f"Claude returned invalid JSON: {raw_json[:200]}") from exc

    # Validate sub-objects
    try:
        cargo = CargoSpec(**data["cargo"])
        logistics_raw = data["logistics"]
        # Normalise ETA: if null, use a sensible default 7 days out
        if not logistics_raw.get("eta"):
            logistics_raw["eta"] = (
                datetime.now(timezone.utc) + timedelta(days=7)
            ).isoformat()
        logistics = LogisticsInfo(**logistics_raw)
        route_raw = data["route"]
        if route_raw.get("total_miles") is None:
            route_raw["total_miles"] = 0
        route = RouteInfo(**route_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise ParserError(f"Claude JSON does not match schema: {exc}") from exc

    load_id = _generate_load_id(db)
    cost_per_mile = _calculate_cost_per_mile(logistics.quoted_rate, route.total_miles)
    rate_anomaly, rate_anomaly_pct = _check_rate_anomaly(
        logistics.quoted_rate, broker_email, db
    )

    return Load(
        load_id=load_id,
        status=LoadStatus.PENDING_APPROVAL,
        cargo=cargo,
        logistics=logistics,
        route=route,
        cost_per_mile=cost_per_mile,
        rate_anomaly=rate_anomaly,
        rate_anomaly_pct=rate_anomaly_pct,
        raw_email_body=email_body,
        broker_email=broker_email,
        created_at=datetime.now(timezone.utc),
    )
