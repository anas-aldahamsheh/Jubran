"""Server maintenance commands.

Create the first administrator, or reset a forgotten admin password, without
putting the password in any file:

    python -m jubran.manage create-admin --email owner@example.com

The password is typed at a hidden prompt. Resetting signs that account out
everywhere.

Give the menu's dishes that have no photo yet the photos shipped with the code
(dishes that already have one are not touched):

    python -m jubran.manage add-menu-photos
"""
import argparse
import asyncio
import getpass
import sys

from jubran.settings import admin_password_problem, settings

MIN_LOCAL_PASSWORD_LENGTH = 8


def password_problem(email: str, password: str) -> str | None:
    if settings.is_production:
        return admin_password_problem(password, email, label="Password")
    if len(password) < MIN_LOCAL_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_LOCAL_PASSWORD_LENGTH} characters."
    return None


async def create_admin(email: str, password: str) -> str:
    from jubran.application.account_security import set_admin_account
    from jubran.infrastructure.db import models  # noqa: F401  (register tables)
    from jubran.infrastructure.db.session import Base, async_session_factory, engine

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with async_session_factory() as session:
            result = await set_admin_account(session, email, password, replace_password=True)
            await session.commit()
        return result
    finally:
        await engine.dispose()


async def add_menu_photos() -> int:
    from jubran.infrastructure.db.seed_photos import add_missing_menu_photos
    from jubran.infrastructure.db.session import async_session_factory, engine

    try:
        async with async_session_factory() as session:
            added = await add_missing_menu_photos(session)
            await session.commit()
        return added
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jubran.manage")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="create an administrator or reset its password")
    create.add_argument("--email", required=True)
    commands.add_parser("add-menu-photos", help="give dishes without a photo the photos shipped with the menu")
    args = parser.parse_args(argv)

    if args.command == "add-menu-photos":
        print(f"Added photos to {asyncio.run(add_menu_photos())} dishes.")
        return 0

    email = args.email.strip().lower()
    password = getpass.getpass("New admin password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    problem = password_problem(email, password)
    if problem:
        print(problem, file=sys.stderr)
        return 1

    result = asyncio.run(create_admin(email, password))
    print(f"Administrator {email} {result}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
