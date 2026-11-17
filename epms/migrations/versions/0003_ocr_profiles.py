from alembic import op

from epms.store import ocr_profiles

revision = "0003"
down_revision = "0002"


def upgrade():
    ocr_profiles.create(op.get_bind(), checkfirst=True)


def downgrade():
    ocr_profiles.drop(op.get_bind())
