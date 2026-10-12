"""Server-side recovery tools (there is exactly one Super Admin, so there is no second admin to reset it).

    python -m app.cli reset-superadmin            # prompts for a new password
    docker exec -it av-boq-api python -m app.cli reset-superadmin

Sets a new password, clears the lockout and signs the account out everywhere. Run it on the server only.
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.core.security import hash_password, validate_password_strength
from app.models import Role, User, UserRoleAssignment


async def reset_superadmin(password: str) -> str:
    """Returns the email of the Super Admin whose password was reset."""
    if err := validate_password_strength(password):
        raise ValueError(err)
    async with SessionLocal() as db:
        user = (
            await db.execute(
                select(User)
                .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
                .join(Role, Role.id == UserRoleAssignment.role_id)
                .where(Role.code == "SUPER_ADMIN")
            )
        ).scalar_one_or_none()
        if user is None:
            raise LookupError("No Super Admin exists yet. Set SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD and run `python -m app.seed`.")
        user.password_hash = hash_password(password)
        user.is_active = True
        user.failed_attempts = 0
        user.locked_until = None
        user.must_change_password = False
        user.token_version += 1
        await db.commit()
        return user.email


async def _main(args: argparse.Namespace) -> int:
    try:
        if args.command == "reset-superadmin":
            password = args.password or getpass.getpass("New Super Admin password: ")
            if not args.password and password != getpass.getpass("Repeat password: "):
                print("Passwords do not match.", file=sys.stderr)
                return 1
            email = await reset_superadmin(password)
            print(f"Password reset for {email}. All their sessions were signed out.")
        return 0
    except (ValueError, LookupError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("reset-superadmin", help="Set a new password for the one Super Admin and unlock the account")
    p.add_argument("--password", help="Avoid on shared shells; it is prompted for when omitted")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
