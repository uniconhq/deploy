"""Whether an OAuth application is reused or replaced on a rerun, and how the
API is reached.

Forgejo shows a client secret once. So the only application bootstrap may keep
is one whose secret .env still holds and whose redirect URI has not moved;
anything else has to be deleted and made again, which invalidates the old one.

The API is driven with curl inside the container, configured over standard
input, so the fake here is a Compose whose exec reads that configuration back
and answers the way Forgejo would.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bootstrap.compose import Compose
from bootstrap.forgejo import (
    API_INSIDE_THE_CONTAINER,
    Forgejo,
    ForgejoError,
    OAuthApplication,
    curl_config,
)

APPLICATIONS = "/api/v1/user/applications/oauth2"
REDIRECT = "http://localhost:8080/api/v1/auth/callback"

KNOWN = OAuthApplication("known-client-id", "known-client-secret")


def _read_config(stdin: str) -> dict[str, list[str]]:
    """The curl configuration bootstrap wrote, as name to values."""
    fields: dict[str, list[str]] = {}
    for line in stdin.splitlines():
        name, _, value = line.partition(" = ")
        fields.setdefault(name, []).append(json.loads(value) if value else "")
    return fields


class FakeForge(Compose):
    """A Compose whose every exec is a curl call to a Forgejo that holds the
    applications given, answering the three calls ensure_oauth_application
    can make and keeping a record of them.
    """

    def __init__(self, existing: list[dict[str, Any]]) -> None:
        super().__init__(Path("."), [])
        self.existing = existing
        self.requests: list[tuple[str, str]] = []
        self.arguments: list[tuple[str, ...]] = []
        self.configs: list[dict[str, list[str]]] = []

    def execute(
        self,
        service: str,
        *arguments: str,
        user: str | None = None,
        stdin: str | None = None,
    ) -> str:
        self.arguments.append(arguments)
        assert stdin is not None
        config = _read_config(stdin)
        self.configs.append(config)
        method = config["request"][0]
        path = config["url"][0].removeprefix(API_INSIDE_THE_CONTAINER)
        self.requests.append((method, "/api/v1" + path))
        if method == "GET":
            return f"{json.dumps(self.existing)}\n200"
        if method == "DELETE":
            return "\n204"
        fresh = {"client_id": "fresh-id", "client_secret": "fresh-secret"}
        return f"{json.dumps(fresh)}\n201"


@pytest.fixture
def forge() -> FakeForge:
    return FakeForge([])


def _ensure(
    forge: FakeForge, known: OAuthApplication | None
) -> tuple[OAuthApplication, bool]:
    forgejo = Forgejo(forge, "forgejo")
    return forgejo.ensure_oauth_application(
        "unicon-backend",
        "password",
        name="Unicon",
        redirect_uri=REDIRECT,
        known=known,
    )


def test_an_application_we_still_hold_the_secret_for_is_kept(forge: FakeForge) -> None:
    forge.existing = [
        {
            "id": 1,
            "name": "Unicon",
            "client_id": KNOWN.client_id,
            "redirect_uris": [REDIRECT],
        }
    ]

    application, created = _ensure(forge, KNOWN)

    assert (application, created) == (KNOWN, False)
    assert [method for method, _ in forge.requests] == ["GET"]


def test_an_application_whose_secret_is_lost_is_replaced(forge: FakeForge) -> None:
    forge.existing = [
        {
            "id": 1,
            "name": "Unicon",
            "client_id": KNOWN.client_id,
            "redirect_uris": [REDIRECT],
        }
    ]

    application, created = _ensure(forge, None)

    assert created is True
    assert application == OAuthApplication("fresh-id", "fresh-secret")
    assert [method for method, _ in forge.requests] == ["GET", "DELETE", "POST"]


def test_an_application_whose_redirect_moved_is_replaced(forge: FakeForge) -> None:
    forge.existing = [
        {
            "id": 1,
            "name": "Unicon",
            "client_id": KNOWN.client_id,
            "redirect_uris": ["http://somewhere-else/api/v1/auth/callback"],
        }
    ]

    _, created = _ensure(forge, KNOWN)

    assert created is True
    assert ("DELETE", f"{APPLICATIONS}/1") in forge.requests


def test_an_application_under_another_client_id_is_replaced(forge: FakeForge) -> None:
    forge.existing = [
        {
            "id": 1,
            "name": "Unicon",
            "client_id": "made-by-somebody-else",
            "redirect_uris": [REDIRECT],
        }
    ]

    _, created = _ensure(forge, KNOWN)

    assert created is True


def test_applications_with_another_name_are_left_alone(forge: FakeForge) -> None:
    forge.existing = [
        {
            "id": 7,
            "name": "Woodpecker",
            "client_id": "woodpeckers-own",
            "redirect_uris": ["http://localhost:8000/authorize"],
        }
    ]

    _, created = _ensure(forge, KNOWN)

    assert created is True
    assert [method for method, _ in forge.requests] == ["GET", "POST"]


def test_the_api_is_reached_inside_the_container_with_nothing_secret_as_an_argument(
    forge: FakeForge,
) -> None:
    _ensure(forge, None)

    assert all(arguments == ("curl", "--config", "-") for arguments in forge.arguments)
    assert forge.configs[0]["user"] == ["unicon-backend:password"]
    assert forge.configs[0]["url"] == [
        f"{API_INSIDE_THE_CONTAINER}/user/applications/oauth2"
    ]
    posted = forge.configs[-1]
    assert json.loads(posted["data"][0])["redirect_uris"] == [REDIRECT]
    assert "Content-Type: application/json" in posted["header"]


def test_a_token_is_checked_with_the_token_as_a_header_and_nothing_else() -> None:
    forge = FakeForge([])

    assert Forgejo(forge, "forgejo").token_is_valid("t0k3n") is True

    assert forge.configs[0]["header"] == [
        "Accept: application/json",
        "Authorization: token t0k3n",
    ]
    assert "user" not in forge.configs[0]
    assert Forgejo(forge, "forgejo").token_is_valid("") is False


def test_a_refusal_names_the_status() -> None:
    class Refusing(FakeForge):
        def execute(self, service: str, *arguments: str, **rest: Any) -> str:
            return '{"message": "no"}\n403'

    with pytest.raises(ForgejoError, match="403"):
        Forgejo(Refusing([]), "forgejo").mint_access_token("unicon-backend", "pw")


def test_every_value_survives_the_curl_configuration_quoting() -> None:
    config = curl_config(
        "POST",
        "/x",
        auth=("a", 'p"a\\ss\nword'),
        token=None,
        body={"name": 'say "hi"\ttab'},
    )

    assert 'user = "a:p\\"a\\\\ss\\nword"' in config
    assert 'data = "{\\"name\\": \\"say \\\\\\"hi\\\\\\"\\\\ttab\\"}"' in config
    assert config.endswith("\n")
