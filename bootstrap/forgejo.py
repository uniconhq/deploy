"""Forgejo: the two bot accounts, the provisioning token and the OAuth apps.

The first account has to be made with the command line inside the container,
because an empty Forgejo has no administrator and therefore no token to call the
admin API with. Everything after that goes through the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from bootstrap.compose import Compose

# All scopes. The provisioning token creates organisations, repositories, teams
# and protected tags, and administers accounts. It is never used to act for a
# person; that is the person's own OAuth token.
PROVISIONING_TOKEN_NAME = "unicon-provisioning"

# The Forgejo binary refuses to run as root, and compose exec is root by
# default, so every command line call has to name the account Forgejo runs as.
CONTAINER_USER = "git"


class ForgejoError(Exception):
    """Forgejo refused something bootstrap needs."""


@dataclass(frozen=True)
class OAuthApplication:
    client_id: str
    client_secret: str


class Forgejo:
    def __init__(self, public_url: str, compose: Compose, service: str) -> None:
        self._base_url = public_url.rstrip("/")
        self._compose = compose
        self._service = service

    # -- account creation, through the container -----------------------------

    def ensure_user(
        self, username: str, password: str, email: str, admin: bool
    ) -> bool:
        """Returns True if this run created the account.

        An account that already exists has its password set again from .env,
        for the reason postgres.py re-applies the role passwords: the Forgejo
        volume can outlive the .env that was generated with it, and then the
        password bootstrap holds opens nothing. Forgejo cannot read a password
        back, so writing it is the only way to make .env the truth.
        """
        if self._user_exists(username):
            self._compose.execute(
                self._service,
                "forgejo",
                "admin",
                "user",
                "change-password",
                "--username",
                username,
                "--password",
                password,
                "--must-change-password=false",
                user=CONTAINER_USER,
            )
            return False
        arguments = [
            "forgejo",
            "admin",
            "user",
            "create",
            "--username",
            username,
            "--password",
            password,
            "--email",
            email,
            "--must-change-password=false",
        ]
        if admin:
            arguments.append("--admin")
        self._compose.execute(self._service, *arguments, user=CONTAINER_USER)
        return True

    def _user_exists(self, username: str) -> bool:
        """Read `forgejo admin user list`, whose second column is the name.

        The table is space-padded and the header row is dropped. Splitting on
        whitespace is safe because no column before the name can hold a space.
        """
        listing = self._compose.execute(
            self._service, "forgejo", "admin", "user", "list", user=CONTAINER_USER
        )
        for line in listing.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2 and fields[1] == username:
                return True
        return False

    # -- everything else, through the API ------------------------------------

    def _client(self, username: str, password: str) -> httpx.Client:
        return httpx.Client(
            base_url=f"{self._base_url}/api/v1",
            auth=(username, password),
            timeout=60.0,
        )

    def token_is_valid(self, token: str) -> bool:
        if not token:
            return False
        response = httpx.get(
            f"{self._base_url}/api/v1/user",
            headers={"Authorization": f"token {token}"},
            timeout=30.0,
        )
        return response.status_code == 200

    def mint_access_token(self, username: str, password: str) -> str:
        """Replace the provisioning token and return the new one.

        Forgejo shows a token once. If .env no longer has a working one there is
        nothing to recover, so the old token is deleted and a new one minted
        rather than the run failing.
        """
        with self._client(username, password) as client:
            existing = _ok(client.get(f"/users/{username}/tokens"))
            for token in existing:
                if token["name"] == PROVISIONING_TOKEN_NAME:
                    _ok(client.delete(f"/users/{username}/tokens/{token['id']}"))
            created = _ok(
                client.post(
                    f"/users/{username}/tokens",
                    json={"name": PROVISIONING_TOKEN_NAME, "scopes": ["all"]},
                )
            )
        return str(created["sha1"])

    def ensure_oauth_application(
        self,
        owner: str,
        owner_password: str,
        name: str,
        redirect_uri: str,
        known: OAuthApplication | None,
    ) -> tuple[OAuthApplication, bool]:
        """Returns the application and whether this run created it.

        A client secret cannot be read back either, so an application whose
        secret we no longer hold, or whose redirect URI has moved, is deleted
        and recreated.
        """
        with self._client(owner, owner_password) as client:
            for application in _ok(client.get("/user/applications/oauth2")):
                if application["name"] != name:
                    continue
                if (
                    known is not None
                    and known.client_id == application["client_id"]
                    and redirect_uri in application["redirect_uris"]
                ):
                    return known, False
                _ok(client.delete(f"/user/applications/oauth2/{application['id']}"))
            created = _ok(
                client.post(
                    "/user/applications/oauth2",
                    json={
                        "name": name,
                        "redirect_uris": [redirect_uri],
                        "confidential_client": True,
                    },
                )
            )
        return OAuthApplication(created["client_id"], created["client_secret"]), True


def _ok(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise ForgejoError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code} {response.text[:200]}"
        )
    return response.json() if response.content else None
