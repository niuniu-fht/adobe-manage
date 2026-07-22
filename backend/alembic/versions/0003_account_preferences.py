"""Add account preferences and migrate low-credit alert semantics.

Revision ID: 0003_account_preferences
Revises: 0002_request_outcomes
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003_account_preferences"
down_revision: Union[str, None] = "0002_request_outcomes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manager_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.bulk_insert(
        sa.table(
            "manager_settings",
            sa.column("key", sa.String()),
            sa.column("value", sa.JSON()),
            sa.column("updated_at", sa.Float()),
        ),
        [{"key": "low_credit_threshold", "value": 100.0, "updated_at": 0.0}],
    )
    op.execute(
        "UPDATE alert_rules SET name='Low-credit accounts', threshold=1, "
        "pending_samples=1, recovery_samples=2 WHERE id='low_credits'"
    )
    op.execute(
        "UPDATE alert_rules SET name='No available accounts' "
        "WHERE id='no_active_tokens'"
    )
    op.execute(
        "UPDATE alert_rules SET name='Account credential expires within 24 hours' "
        "WHERE id='token_expiring'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE alert_rules SET name='Low available credits', threshold=0.20, "
        "pending_samples=2, recovery_samples=2 WHERE id='low_credits'"
    )
    op.drop_table("manager_settings")
