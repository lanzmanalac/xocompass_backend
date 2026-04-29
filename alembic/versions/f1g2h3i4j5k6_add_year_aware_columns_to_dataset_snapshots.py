# alembic/versions/f1g2h3i4j5k6_add_year_aware_columns_to_dataset_snapshots.py
"""add year-aware KPI columns to dataset_snapshots

Revision ID: f1g2h3i4j5k6
Revises: e2f3a4b5c6d7
Create Date: 2026-04-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f1g2h3i4j5k6'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All additive — existing rows get NULL, which the endpoint handles
    # gracefully via .get() fallbacks. No data loss. No backfill needed.
    op.add_column('dataset_snapshots',
        sa.Column('top_routes_json', sa.JSON(), nullable=True))
    op.add_column('dataset_snapshots',
        sa.Column('revenue_by_year_json', sa.JSON(), nullable=True))
    op.add_column('dataset_snapshots',
        sa.Column('data_quality_json', sa.JSON(), nullable=True))
    op.add_column('dataset_snapshots',
        sa.Column('available_years_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('dataset_snapshots', 'available_years_json')
    op.drop_column('dataset_snapshots', 'data_quality_json')
    op.drop_column('dataset_snapshots', 'revenue_by_year_json')
    op.drop_column('dataset_snapshots', 'top_routes_json')