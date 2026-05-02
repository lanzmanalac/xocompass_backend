"""audit_immutability

Revision ID: 0ab1bdfdb999
Revises: 079d319f2170
Create Date: 2026-05-02 23:05:48.994985

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0ab1bdfdb999'
down_revision: Union[str, Sequence[str], None] = '079d319f2170'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # We use CREATE OR REPLACE and DROP IF EXISTS so this runs cleanly
    # even though we manually tested the SQL earlier!
    op.execute("""
    CREATE OR REPLACE FUNCTION prevent_audit_update()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_logs is append-only; UPDATE is not permitted';
    END;
    $$ LANGUAGE plpgsql;

    CREATE OR REPLACE FUNCTION prevent_audit_delete()
    RETURNS TRIGGER AS $$
    BEGIN
        RAISE EXCEPTION 'audit_logs is append-only; DELETE is not permitted';
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS enforce_append_only_update ON audit_logs;
    CREATE TRIGGER enforce_append_only_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_update();

    DROP TRIGGER IF EXISTS enforce_append_only_delete ON audit_logs;
    CREATE TRIGGER enforce_append_only_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_delete();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS enforce_append_only_update ON audit_logs;
    DROP TRIGGER IF EXISTS enforce_append_only_delete ON audit_logs;
    DROP FUNCTION IF EXISTS prevent_audit_update();
    DROP FUNCTION IF EXISTS prevent_audit_delete();
    """)