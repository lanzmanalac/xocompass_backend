"""Create the table for the latest published forecast."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e2a4b6c8d0f1"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create one slot for the current report."""
    op.create_table(
        "published_forecast",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("run_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_published_forecast_single_slot"),
    )


def downgrade() -> None:
    """Remove the report table."""
    op.drop_table("published_forecast")
