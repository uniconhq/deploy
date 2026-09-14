"""Whether an OAuth application is reused or replaced on a rerun.

Forgejo shows a client secret once. So the only application bootstrap may keep
is one whose secret .env still holds and whose redirect URI has not moved;
anything else has to be deleted and made again, which invalidates the old one.
The Forgejo API is faked here with httpx.MockTransport.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from bootstrap.compose import Compose
from bootstrap.forgejo import Forgejo, OAuthApplication

APPLICATIONS = "/api/v1/user/applications/oauth2"
REDIRECT = "http://localhost:8080/api/v1/auth/callback"

KNOWN = OAuthApplication("known-client-id", "known-client-secret")


class FakeForge:
    """The three calls ensure_oauth_application can make, and a record of them."""

    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.requests: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json=self.existing)
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            201, json={"client_id": "fresh-id", "client_secret": "fresh-secret"}
        )


@pytest.fixture
def forge(monkeypatch: pytest.MonkeyPatch) -> FakeForge:
    """Point every httpx.Client bootstrap opens at a fake Forgejo."""
    fake = FakeForge([])
    real_client = httpx.Client

    def build(**kwargs: Any) -> httpx.Client:
        return real_client(**kwargs, transport=httpx.MockTransport(fake.handle))

    monkeypatch.setattr(httpx, "Client", build)
    return fake


def _ensure(known: OAuthApplication | None) -> tuple[OAuthApplication, bool]:
    forgejo = Forgejo("http://forgejo.invalid", Compose(Path("."), []), "forgejo")
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

    application, created = _ensure(KNOWN)

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

    application, created = _ensure(None)

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

    _, created = _ensure(KNOWN)

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

    _, created = _ensure(KNOWN)

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

    _, created = _ensure(KNOWN)

    assert created is True
    assert [method for method, _ in forge.requests] == ["GET", "POST"]
