"""add correlation_json to model_diagnostics and risk_flag to forecast_cache

Revision ID: e3f4g5h6i7j8
Revises: d1e2f3a4b5c6
Create Date: 2026-04-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e3f4g5h6i7j8'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Exogenous correlation scores for the heatmap ──────────────────
    # Stores [{variable: str, correlation: float}] computed at training time.
    # Written by orchestrator.py step2_correlations, read by
    # GET /api/advanced-metrics/{model_id}.
    # NULL-safe: old models will return [] from the API, not crash.
    op.add_column(
        'model_diagnostics',
        sa.Column('correlation_json', sa.JSON(), nullable=True)
    )

    # ── 2. Risk classification for Critical Forecast Weeks table ──────────
    # "HIGH" | "MEDIUM" | "LOW" | null (unclassified).
    # Logic is intentionally deferred — column exists so the pipe works.
    # Hardcoded mock values written by seed_mock_data.py for now.
    op.add_column(
        'forecast_cache',
        sa.Column('risk_flag', sa.String(10), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('forecast_cache', 'risk_flag')
    op.drop_column('model_diagnostics', 'correlation_json')