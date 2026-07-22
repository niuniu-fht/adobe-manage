import time
import uuid
from typing import Any, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now_ts() -> float:
    return time.time()


class Instance(Base):
    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    location: Mapped[str] = mapped_column(String(160), default="")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seen_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_failure_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_latency_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    ops_api_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=now_ts, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=now_ts, onupdate=now_ts, nullable=False)

    samples: Mapped[list["MetricSample"]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )


class MetricSample(Base):
    __tablename__ = "metric_samples"
    __table_args__ = (Index("ix_metric_instance_ts", "instance_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[float] = mapped_column(Float, default=now_ts, nullable=False, index=True)
    online: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    request_total: Mapped[int] = mapped_column(Integer, default=0)
    successful_requests: Mapped[int] = mapped_column(Integer, default=0)
    failed_requests: Mapped[int] = mapped_column(Integer, default=0)
    error_rate: Mapped[float] = mapped_column(Float, default=0.0)
    duration_p95_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    active_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    credits_available: Mapped[float] = mapped_column(Float, default=0.0)
    credits_total: Mapped[float] = mapped_column(Float, default=0.0)
    in_progress: Mapped[int] = mapped_column(Integer, default=0)

    instance: Mapped[Instance] = relationship(back_populates="samples")


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    minimum_requests: Mapped[int] = mapped_column(Integer, default=0)
    pending_samples: Mapped[int] = mapped_column(Integer, default=1)
    recovery_samples: Mapped[int] = mapped_column(Integer, default=2)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_instance_rule_state", "instance_id", "rule_id", "state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[str] = mapped_column(
        ForeignKey("instances.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(ForeignKey("alert_rules.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    message: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    consecutive_hits: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_recoveries: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[float] = mapped_column(Float, default=now_ts)
    firing_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolved_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_notified_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float, default=now_ts, onupdate=now_ts)


class AlertSilence(Base):
    __tablename__ = "alert_silences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("instances.id", ondelete="CASCADE"), nullable=True
    )
    rule_id: Mapped[Optional[str]] = mapped_column(ForeignKey("alert_rules.id"), nullable=True)
    starts_at: Mapped[float] = mapped_column(Float, default=now_ts)
    ends_at: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), default="")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_ts", "ts"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ts: Mapped[float] = mapped_column(Float, default=now_ts, nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("instances.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), default="")
    resource_id: Mapped[str] = mapped_column(String(160), default="")
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    request_id: Mapped[str] = mapped_column(String(80), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
