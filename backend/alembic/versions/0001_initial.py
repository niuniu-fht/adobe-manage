"""Initial manager schema.

Revision ID: 0001_initial
Revises:
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("location", sa.String(length=160), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("consecutive_successes", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.Float(), nullable=True),
        sa.Column("last_failure_at", sa.Float(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("last_latency_seconds", sa.Float(), nullable=True),
        sa.Column("last_snapshot", sa.JSON(), nullable=True),
        sa.Column("ops_api_version", sa.Integer(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("base_url"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("minimum_requests", sa.Integer(), nullable=False),
        sa.Column("pending_samples", sa.Integer(), nullable=False),
        sa.Column("recovery_samples", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.Column("request_total", sa.Integer(), nullable=False),
        sa.Column("error_rate", sa.Float(), nullable=False),
        sa.Column("duration_p95_seconds", sa.Float(), nullable=False),
        sa.Column("active_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("credits_available", sa.Float(), nullable=False),
        sa.Column("credits_total", sa.Float(), nullable=False),
        sa.Column("in_progress", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metric_instance_ts", "metric_samples", ["instance_id", "ts"])
    op.create_index("ix_metric_samples_ts", "metric_samples", ["ts"])
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("consecutive_hits", sa.Integer(), nullable=False),
        sa.Column("consecutive_recoveries", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.Float(), nullable=False),
        sa.Column("firing_at", sa.Float(), nullable=True),
        sa.Column("resolved_at", sa.Float(), nullable=True),
        sa.Column("last_notified_at", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_instance_rule_state", "alert_events", ["instance_id", "rule_id", "state"])
    op.create_table(
        "alert_silences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=True),
        sa.Column("rule_id", sa.String(length=80), nullable=True),
        sa.Column("starts_at", sa.Float(), nullable=False),
        sa.Column("ends_at", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=160), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_ts", "audit_events", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_ts", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("alert_silences")
    op.drop_index("ix_alert_instance_rule_state", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_index("ix_metric_samples_ts", table_name="metric_samples")
    op.drop_index("ix_metric_instance_ts", table_name="metric_samples")
    op.drop_table("metric_samples")
    op.drop_table("alert_rules")
    op.drop_table("instances")
