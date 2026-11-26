"""Reusable office preferences and original payment statements."""
from alembic import op
from epms.workbench import TABLES
revision='0010'
down_revision='0009'
def upgrade():
    for table in TABLES:table.create(op.get_bind(),checkfirst=True)
def downgrade():raise RuntimeError('업무 기본정보·명세 원본은 삭제하지 않습니다. 변경 전 백업으로 복원하세요.')
