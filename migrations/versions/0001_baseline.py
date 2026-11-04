"""Create the MVP baseline or adopt its existing compatible tables without deleting data."""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def ensure_table(name, columns):
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(name):
        found = {column["name"] for column in inspector.get_columns(name)}
        if not {column.name for column in columns}.issubset(found):
            raise RuntimeError("Existing database schema is incompatible with the MVP baseline")
        return
    op.create_table(name, *columns)


def upgrade():
    ensure_table("analysis_runs", [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
    ])
    ensure_table("knowledge_documents", [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ])
    ensure_table("rule_definitions", [
        sa.Column("code", sa.String(60), primary_key=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
    ])


def downgrade():
    raise RuntimeError("Restore a verified backup instead of deleting baseline data")
