"""drop forecast_snapshots, create business_analytics_snapshots, add kpi columns to sarimax_models

Revision ID: c1d2e3f4g5h6
Revises: d2a7179525ad
Create Date: 2026-04-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c1d2e3f4g5h6'
down_revision: Union[str, Sequence[str], None] = 'd2a7179525ad'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Drop the old overloaded forecast_snapshots table ──
    op.drop_table('forecast_snapshots')

    # ── 2. Add KPI columns directly to sarimax_models ──
    # These columns are written by orchestrator.py at training time
    # and read by GET /api/dashboard-stats/{model_id}
    op.add_column('sarimax_models', sa.Column('total_records', sa.Integer(), nullable=True))
    op.add_column('sarimax_models', sa.Column('data_quality_pct', sa.Float(), nullable=True))
    op.add_column('sarimax_models', sa.Column('revenue_total', sa.Float(), nullable=True))
    op.add_column('sarimax_models', sa.Column('growth_rate', sa.Float(), nullable=True))
    op.add_column('sarimax_models', sa.Column('expected_bookings', sa.Integer(), nullable=True))
    op.add_column('sarimax_models', sa.Column('peak_travel_period', sa.String(100), nullable=True))
    op.add_column('sarimax_models', sa.Column('yearly_bookings_json', sa.JSON(), nullable=True))

    # ── 3. Create business_analytics_snapshots (dataset-scoped, no model_id) ──
    op.create_table(
        'business_analytics_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_transaction_count', sa.Integer(), nullable=True),
        sa.Column('total_weekly_records', sa.Integer(), nullable=True),
        sa.Column('total_revenue', sa.Float(), nullable=True),
        sa.Column('data_start_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data_end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('span_weeks', sa.Integer(), nullable=True),
        sa.Column('avg_weekly_bookings', sa.Float(), nullable=True),
        sa.Column('peak_week_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('peak_week_bookings', sa.Integer(), nullable=True),
        sa.Column('growth_rate', sa.Float(), nullable=True),
        sa.Column('bookings_by_year_json', sa.JSON(), nullable=True),
        sa.Column('bookings_by_month_json', sa.JSON(), nullable=True),
        sa.Column('holiday_week_count', sa.Integer(), nullable=True),
        sa.Column('non_holiday_week_count', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('business_analytics_snapshots')
    op.drop_column('sarimax_models', 'yearly_bookings_json')
    op.drop_column('sarimax_models', 'peak_travel_period')
    op.drop_column('sarimax_models', 'expected_bookings')
    op.drop_column('sarimax_models', 'growth_rate')
    op.drop_column('sarimax_models', 'revenue_total')
    op.drop_column('sarimax_models', 'data_quality_pct')
    op.drop_column('sarimax_models', 'total_records')
    op.create_table(
        'forecast_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model_id', sa.Integer(), sa.ForeignKey('sarimax_models.id'), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_records', sa.Integer(), nullable=True),
        sa.Column('data_quality_pct', sa.Float(), nullable=True),
        sa.Column('revenue_total', sa.Float(), nullable=True),
        sa.Column('growth_rate', sa.Float(), nullable=True),
        sa.Column('expected_bookings', sa.Integer(), nullable=True),
        sa.Column('peak_travel_period', sa.String(length=100), nullable=True),
        sa.Column('yearly_bookings_json', sa.JSON(), nullable=True),
    )