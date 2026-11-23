"""fix auth schema drift from superseded 37c7b3a88ff7 audit_logs

Revision ID: c4d5e6f7a8b9
Revises: ad2f804fb989
Create Date: 2026-06-30

Some Neon databases were migrated through 37c7b3a88ff7 before that revision
was turned into a no-op. Alembic then stamped 079d319f2170+ without recreating
audit_logs, leaving `created_at` and a stale audit_status enum (FAILURE/PENDING)
while the ORM expects `timestamp` and FAILED.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "ad2f804fb989"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _audit_logs_needs_rebuild(insp: sa.Inspector) -> bool:
    if not insp.has_table("audit_logs"):
        return True
    cols = {c["name"] for c in insp.get_columns("audit_logs")}
    return "timestamp" not in cols


def _recreate_audit_triggers_postgres() -> None:
    op.execute(
        """
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
        """
    )


def _rebuild_audit_logs_postgres() -> None:
    op.execute("DROP TRIGGER IF EXISTS enforce_append_only_update ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS enforce_append_only_delete ON audit_logs")
    op.drop_table("audit_logs")
    op.execute("DROP TYPE IF EXISTS audit_status")

    audit_status_enum = postgresql.ENUM(
        "SUCCESS", "FAILED", name="audit_status", create_type=False
    )
    op.execute("CREATE TYPE audit_status AS ENUM ('SUCCESS', 'FAILED')")

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("user_email_snapshot", sa.String(length=255), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("target_resource", sa.String(length=120), nullable=True),
        sa.Column("status", audit_status_enum, nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_action_status", "audit_logs", ["action_type", "status"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_action_type"), "audit_logs", ["action_type"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_module"), "audit_logs", ["module"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_timestamp"), "audit_logs", ["timestamp"], unique=False
    )
    op.create_index(
        "ix_audit_timestamp_desc",
        "audit_logs",
        [sa.literal_column("timestamp DESC")],
        unique=False,
    )
    _recreate_audit_triggers_postgres()


def _rebuild_audit_logs_sqlite() -> None:
    op.execute("DROP TRIGGER IF EXISTS enforce_append_only_update")
    op.execute("DROP TRIGGER IF EXISTS enforce_append_only_delete")
    op.drop_table("audit_logs")

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("user_email_snapshot", sa.String(length=255), nullable=True),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("target_resource", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            sa.Enum("SUCCESS", "FAILED", name="audit_status"),
            nullable=False,
        ),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_action_status", "audit_logs", ["action_type", "status"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_action_type"), "audit_logs", ["action_type"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_module"), "audit_logs", ["module"], unique=False
    )
    op.create_index(
        op.f("ix_audit_logs_timestamp"), "audit_logs", ["timestamp"], unique=False
    )
    op.execute(
        """
        CREATE TRIGGER enforce_append_only_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs is append-only; UPDATE is not permitted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER enforce_append_only_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW
        BEGIN
            SELECT RAISE(ABORT, 'audit_logs is append-only; DELETE is not permitted');
        END
        """
    )


def _upgrade_refresh_tokens(insp: sa.Inspector) -> None:
    if not insp.has_table("refresh_tokens"):
        return
    cols = {c["name"] for c in insp.get_columns("refresh_tokens")}
    if "issued_at" in cols:
        return

    if "created_at" in cols:
        with op.batch_alter_table("refresh_tokens") as batch_op:
            batch_op.alter_column("created_at", new_column_name="issued_at")
            cols.remove("created_at")
            cols.add("issued_at")

    if "revoked" in cols:
        op.drop_column("refresh_tokens", "revoked")

    if "consumed_at" not in cols:
        op.add_column(
            "refresh_tokens",
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "replaced_by_id" not in cols:
        op.add_column(
            "refresh_tokens",
            sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        )
        op.create_foreign_key(
            "fk_refresh_tokens_replaced_by_id",
            "refresh_tokens",
            "refresh_tokens",
            ["replaced_by_id"],
            ["id"],
        )
    if "user_agent" not in cols:
        op.add_column(
            "refresh_tokens",
            sa.Column("user_agent", sa.String(length=500), nullable=True),
        )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if _audit_logs_needs_rebuild(insp):
        if bind.dialect.name == "sqlite":
            _rebuild_audit_logs_sqlite()
        else:
            _rebuild_audit_logs_postgres()

    _upgrade_refresh_tokens(sa.inspect(bind))


def downgrade() -> None:
    # Irreversible: old drifted schema is not worth restoring.
    pass
