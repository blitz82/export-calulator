"""Monthly reporting and immutable submission packages."""
from alembic import op
from epms.monthly_report import TABLES
revision='0009'
down_revision='0008'
def upgrade():
    for table in TABLES:table.create(op.get_bind(),checkfirst=True)
def downgrade():raise RuntimeError('월별 보고·지급 이력은 삭제하지 않습니다. 변경 전 백업으로 복원하세요.')
