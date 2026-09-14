"""Reading `forgejo admin user list`, the only way to ask whether an account exists.

An empty Forgejo has no administrator and so no token to call the admin API
with, which is why bootstrap parses a table meant for a person.
"""

from __future__ import annotations

from pathlib import Path

from bootstrap.compose import Compose
from bootstrap.forgejo import Forgejo

# Real output from forgejo 15.0.8. The columns are space-padded to the widest
# value, so the gaps between them vary from row to row.
USER_LIST = """ID   Username        Email                         IsActive IsAdmin 2FA
1    unicon-backend  unicon-backend@unicon.invalid true     true    false
2    unicon-ci       unicon-ci@unicon.invalid      true     false   false
3    contestant-demo contestant-demo@example.org   true     false   false
"""


class FakeCompose(Compose):
    """A Compose whose every exec returns the same canned stdout."""

    def __init__(self, output: str) -> None:
        super().__init__(Path("."), [])
        self._output = output
        self.calls: list[tuple[str, ...]] = []

    def execute(
        self,
        service: str,
        *arguments: str,
        user: str | None = None,
        stdin: str | None = None,
    ) -> str:
        self.calls.append(arguments)
        return self._output


def _forgejo(output: str) -> tuple[Forgejo, FakeCompose]:
    compose = FakeCompose(output)
    return Forgejo("http://forgejo.invalid", compose, "forgejo"), compose


def test_an_existing_account_is_not_created_again() -> None:
    forgejo, compose = _forgejo(USER_LIST)

    created = forgejo.ensure_user("unicon-ci", "pw", "ci@unicon.invalid", admin=False)

    assert created is False
    assert [call[:4] for call in compose.calls] == [
        ("forgejo", "admin", "user", "list"),
        ("forgejo", "admin", "user", "change-password"),
    ]


def test_the_password_is_re_applied_to_an_existing_account() -> None:
    """The Forgejo volume can outlive the .env generated with it. Writing the
    password back is the only way to make .env the truth again."""
    forgejo, compose = _forgejo(USER_LIST)

    forgejo.ensure_user("unicon-ci", "a new password", "ci@unicon.invalid", admin=False)

    assert compose.calls[-1] == (
        "forgejo",
        "admin",
        "user",
        "change-password",
        "--username",
        "unicon-ci",
        "--password",
        "a new password",
        "--must-change-password=false",
    )


def test_a_missing_account_is_created_as_an_administrator() -> None:
    forgejo, compose = _forgejo(USER_LIST)

    created = forgejo.ensure_user("someone-else", "pw", "e@unicon.invalid", admin=True)

    assert created is True
    assert compose.calls[-1][:4] == ("forgejo", "admin", "user", "create")
    assert compose.calls[-1][-1] == "--admin"


def test_a_name_in_another_column_is_not_a_match() -> None:
    """The email column holds the username too. Only the second field counts."""
    forgejo, _ = _forgejo(USER_LIST)

    assert forgejo.ensure_user(
        "unicon-ci@unicon.invalid", "pw", "e@unicon.invalid", admin=False
    )


def test_the_header_row_is_not_an_account() -> None:
    forgejo, _ = _forgejo(USER_LIST)

    assert forgejo.ensure_user("Username", "pw", "e@unicon.invalid", admin=False)
