"""Independent two-sided allocations and immutable settlement PDFs."""
from alembic import op
from epms.settlement import TABLES
revision='0006'
down_revision='0005'
def upgrade():
    for table in TABLES:table.create(op.get_bind(),checkfirst=True)
def downgrade():
    raise RuntimeError('정산 이력은 삭제하지 않습니다. 변경 전 백업으로 복원하세요.')
