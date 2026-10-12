"""Exactly one Super Admin: DB triggers refuse a second holder and removal of the role from the first.

Revision ID: a1b2c3d4e5f6
Revises: 7fd663e78185
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "7fd663e78185"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FN_INSERT = """
CREATE OR REPLACE FUNCTION enforce_single_super_admin() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM roles WHERE id = NEW.role_id AND code = 'SUPER_ADMIN') THEN
        IF EXISTS (
            SELECT 1 FROM user_role_assignments a JOIN roles r ON r.id = a.role_id
            WHERE r.code = 'SUPER_ADMIN' AND a.user_id <> NEW.user_id
        ) THEN
            RAISE EXCEPTION 'There can be only one Super Admin' USING ERRCODE = '23505';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FN_DELETE = """
CREATE OR REPLACE FUNCTION protect_super_admin_assignment() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM roles WHERE id = OLD.role_id AND code = 'SUPER_ADMIN')
       AND EXISTS (SELECT 1 FROM users WHERE id = OLD.user_id) THEN
        RAISE EXCEPTION 'The Super Admin role cannot be removed from its holder' USING ERRCODE = '23503';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(FN_INSERT)
    op.execute(
        "CREATE TRIGGER trg_single_super_admin BEFORE INSERT OR UPDATE ON user_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION enforce_single_super_admin()"
    )
    op.execute(FN_DELETE)
    op.execute(
        "CREATE TRIGGER trg_protect_super_admin BEFORE DELETE ON user_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION protect_super_admin_assignment()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_protect_super_admin ON user_role_assignments")
    op.execute("DROP FUNCTION IF EXISTS protect_super_admin_assignment()")
    op.execute("DROP TRIGGER IF EXISTS trg_single_super_admin ON user_role_assignments")
    op.execute("DROP FUNCTION IF EXISTS enforce_single_super_admin()")
