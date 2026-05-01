"""restore kpi columns to sarimax_models — dropped by accident in d8fb31cb8ab8

Revision ID: f1a2b3c4d5e6
Revises: d8fb31cb8ab8
Create Date: 2026-05-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'd8fb31cb8ab8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Re-add the 7 KPI columns that d8fb31cb8ab8 incorrectly dropped.
    # These are written by orchestrator.py -> persist_to_neon() -> compute_snapshot_kpis()
    # and read by GET /api/dashboard-stats/{model_id}.
    # Using IF NOT EXISTS logic via try/except to make this idempotent
    # in case some environments still have the columns.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = {col['name'] for col in inspector.get_columns('sarimax_models')}

    if 'total_records' not in existing_cols:
        op.add_column('sarimax_models', sa.Column('total_records', sa.Integer(), nullable=True))
    if 'data_quality_pct' not in existing_cols:
        op.add_column('sarimax_models', sa.Column('data_quality_pct', sa.Float(), nullable=True))
    if 'revenue_total' not in existing_cols:
        op.add_column('sarimax_models', sa.Column('revenue_total', sa.Float(), nullable=True))
    if 'growth_rate' not in existing_cols:
        op.add_column('sarimax_models', sa.Column('growth_rate', sa.Float(), nullable=True))
    if 'expected_bookings' not in existing_cols:
        op.add_column('sarimax_models', sa.Column('expected_bookings', sa.Integer(), nullable=True))
    if 'peak_travel_period' not in existing_cols:
        op.add_column('sarimax_models', sa.Column('peak_travel_period', sa.String(100), nullable=True))
    if 'yearly_bookings_json' not in existing_cols:
        op.add_column('sarimax_models', sa.Column('yearly_bookings_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('sarimax_models', 'yearly_bookings_json')
    op.drop_column('sarimax_models', 'peak_travel_period')
    op.drop_column('sarimax_models', 'expected_bookings')
    op.drop_column('sarimax_models', 'growth_rate')
    op.drop_column('sarimax_models', 'revenue_total')
    op.drop_column('sarimax_models', 'data_quality_pct')
    op.drop_column('sarimax_models', 'total_records')