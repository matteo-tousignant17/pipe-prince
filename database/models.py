from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, JSON, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class LoadORM(Base):
    __tablename__ = "loads"

    load_id = Column(String, primary_key=True)
    status = Column(String, nullable=False, default="pending_approval")
    cargo_json = Column(JSON, nullable=False)
    logistics_json = Column(JSON, nullable=False)
    route_json = Column(JSON, nullable=False)
    cost_per_mile = Column(Float, nullable=True)
    rate_anomaly = Column(Boolean, nullable=True)
    rate_anomaly_pct = Column(Float, nullable=True)
    raw_email_body = Column(Text, nullable=False)
    broker_email = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)
    rate_con_pdf_path = Column(String, nullable=True)
    release_doc_pdf_path = Column(String, nullable=True)
    slack_message_ts = Column(String, nullable=True)
    slack_channel_id = Column(String, nullable=True)


class BrokerProfileORM(Base):
    __tablename__ = "broker_profiles"

    broker_email = Column(String, primary_key=True)
    broker_name = Column(String, nullable=False)
    first_seen = Column(DateTime, nullable=False)
    load_count = Column(Integer, default=0)
    correction_examples = Column(JSON, default=list)


class ValidationRecordORM(Base):
    __tablename__ = "validation_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    load_id = Column(String, nullable=False, index=True)
    validator_slack_user = Column(String, nullable=False)
    action = Column(String, nullable=False)  # "approve", "reject", "edit"
    original_load = Column(JSON, nullable=False)
    corrected_load = Column(JSON, nullable=True)
    note = Column(Text, nullable=True)
    validated_at = Column(DateTime, nullable=False)
