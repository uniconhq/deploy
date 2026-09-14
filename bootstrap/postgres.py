"""Keeping the database role passwords in step with .env.

postgres/init only runs on the first start of an empty data directory. A stack
whose .env was regenerated while the volume survived would then have three
passwords nobody can use, and the only symptom is Forgejo failing to
authenticate. Setting them on every run makes .env the truth instead.

The connection is over the container socket, where the image trusts local
connections, so this works whether or not the superuser password in .env
matches the one the volume was built with.
"""

from __future__ import annotations

from bootstrap.compose import Compose

ROLES = ("forgejo", "woodpecker", "unicon")


def sync_role_passwords(
    compose: Compose, service: str, passwords: dict[str, str]
) -> None:
    # Over stdin rather than --command: an argument would show up in the
    # container process list and in anything that quoted the failing command.
    statements = "".join(
        f"ALTER ROLE {role} WITH PASSWORD '{_quote(passwords[role])}';\n"
        for role in ROLES
    )
    compose.execute(
        service,
        "psql",
        "--username",
        "postgres",
        "--dbname",
        "postgres",
        "--quiet",
        "-v",
        "ON_ERROR_STOP=1",
        stdin=statements,
    )


def _quote(password: str) -> str:
    """Escape for a single-quoted SQL literal.

    Passwords here are generated URL-safe so this never fires, but a password
    typed into .env by hand should not be able to end the statement.
    """
    return password.replace("'", "''")
