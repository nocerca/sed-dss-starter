"""Assistant operation markers and bounded responses, linked to report retention."""

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'assistant_operations',
        sa.Column('key_hash', sa.String(64), primary_key=True),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('report_id', sa.String(36),
                  sa.ForeignKey('analysis_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_assistant_operations_report_id', 'assistant_operations', ['report_id'])


def downgrade() -> None:
    raise RuntimeError('Destructive downgrade is not supported')
