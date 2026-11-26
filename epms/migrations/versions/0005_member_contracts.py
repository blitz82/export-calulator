"""Add member agreements, original PDFs and immutable revision snapshots."""
from alembic import op
from epms.member_contracts import TABLES

revision = '0005'
down_revision = '0004'


def upgrade():
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('계약 원본과 이력은 강제 삭제하지 않습니다. 변경 전 백업으로 복원하세요.')
