"""Annual support plans and explicit per-member budget usage."""
from alembic import op
from epms.support_budget import TABLES
revision='0008'
down_revision='0007'
def upgrade():
    for table in TABLES:table.create(op.get_bind(),checkfirst=True)
def downgrade():raise RuntimeError('지원기준·배정 이력은 삭제하지 않습니다. 변경 전 백업으로 복원하세요.')
