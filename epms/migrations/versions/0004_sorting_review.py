"""Add isolated review-only sorting tables without changing export records."""
from alembic import op
from epms.sorting import TABLES

revision = '0004'
down_revision = '0003'


def upgrade():
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('공동선별 기록은 강제 downgrade하지 않습니다. 변경 전 백업으로 복원하세요.')
