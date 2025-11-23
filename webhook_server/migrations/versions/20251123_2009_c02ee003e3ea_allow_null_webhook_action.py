"""Allow NULL values for webhook action column

Revision ID: c02ee003e3ea
Revises: 450c7d70bcaa
Create Date: 2025-11-23 20:09:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c02ee003e3ea"
down_revision: str | None = "450c7d70bcaa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make action column nullable in webhooks table.

    Some GitHub webhook events (push, create, delete, fork, watch) don't have
    an 'action' field, so the column must allow NULL values.
    """
    # Modify action column to allow NULL
    op.alter_column("webhooks", "action", existing_type=sa.String(length=50), nullable=True)


def downgrade() -> None:
    """Revert action column to NOT NULL (not recommended - will fail if NULL values exist)."""
    # This downgrade will fail if any NULL values exist in the action column
    op.alter_column("webhooks", "action", existing_type=sa.String(length=50), nullable=False)
