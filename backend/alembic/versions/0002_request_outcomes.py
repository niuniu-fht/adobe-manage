"""Add successful and failed request samples.

Revision ID: 0002_request_outcomes
Revises: 0001_initial
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_request_outcomes"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "metric_samples",
        sa.Column("successful_requests", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "metric_samples",
        sa.Column("failed_requests", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("metric_samples", "failed_requests")
    op.drop_column("metric_samples", "successful_requests")
