"""verdicts read from the disk of a stopped VM

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from jatszoter_api.models import CheckStatus

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK_STATUS = sa.Enum(CheckStatus, native_enum=False)


def upgrade() -> None:
    op.create_table(
        "disk_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("status", CHECK_STATUS, nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deployment_id"),
    )


def downgrade() -> None:
    op.drop_table("disk_checks")
