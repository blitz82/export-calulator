from alembic import op

from epms.store import office

revision = "0002"
down_revision = "0001"


def upgrade():
    office.create(op.get_bind(), checkfirst=True)


def downgrade():
    office.drop(op.get_bind())
