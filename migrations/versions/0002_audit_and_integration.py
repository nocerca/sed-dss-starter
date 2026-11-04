"""Add rule versions and request metadata; preserve existing reports."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    for column in [
        sa.Column("external_document_id", sa.String(128), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("ruleset_version", sa.String(40), nullable=True),
        sa.Column("document_type", sa.String(30), nullable=True),
        sa.Column("template_id", sa.String(64), nullable=True),
    ]:
        op.add_column("analysis_runs", column)
    op.create_table("rule_versions",
        sa.Column("code", sa.String(60), primary_key=True),
        sa.Column("version", sa.String(32), primary_key=True),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("idempotency_records",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting audit data")
