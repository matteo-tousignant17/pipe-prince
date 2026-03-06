from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from typing import Optional


class LoadStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    SHADOW = "shadow"


class CargoSpec(BaseModel):
    type: str
    quantity: int
    unit: str
    specs: str


class LogisticsInfo(BaseModel):
    broker_name: str
    quoted_rate: float
    currency: str = "USD"
    eta: datetime


class RouteInfo(BaseModel):
    origin: str
    destination: str
    total_miles: int


class Load(BaseModel):
    load_id: str
    status: LoadStatus = LoadStatus.PENDING_APPROVAL
    cargo: CargoSpec
    logistics: LogisticsInfo
    route: RouteInfo
    cost_per_mile: Optional[float] = None
    rate_anomaly: Optional[bool] = None
    rate_anomaly_pct: Optional[float] = None
    raw_email_body: str
    broker_email: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BrokerProfile(BaseModel):
    broker_email: str
    broker_name: str
    first_seen: datetime
    load_count: int = 0
    correction_examples: list[dict] = []


class ValidationRecord(BaseModel):
    load_id: str
    validator_slack_user: str
    action: str
    original_load: dict
    corrected_load: Optional[dict] = None
    note: Optional[str] = None
    validated_at: datetime = Field(default_factory=datetime.utcnow)
