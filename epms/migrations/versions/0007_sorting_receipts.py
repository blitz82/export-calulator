"""Independent facility/period intake groups, preserving all existing record IDs."""
from alembic import op
from epms.sorting_receipts import receipts
revision='0007'
down_revision='0006'
def upgrade():receipts.create(op.get_bind(),checkfirst=True)
def downgrade():raise RuntimeError('접수·증빙 이력은 삭제하지 않습니다. 변경 전 백업으로 복원하세요.')
