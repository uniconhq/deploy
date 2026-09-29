"""Forgejo: the two service accounts, the provisioning token and the OAuth apps.

Forgejo's API is reachable on the compose network and nowhere else: the proxy
routes people to its sign-in and account pages and nothing more. So bootstrap
drives the API from inside the Forgejo container, with the curl the image
ships, the same way it drives Garage through the container's own command line.
The first account has to be made with the command line inside the container
anyway, because an empty Forgejo has no administrator and therefore no token
to call the admin API with.

Every command line call runs as CONTAINER_USER, the account Forgejo runs as:
the Forgejo binary refuses to run as root, and compose exec is root by default.

Nothing secret goes on a command line. curl reads its whole configuration,
the URL, the method, the credentials and the body, from standard input, so a
password or a token is never an argument that `ps` or a shell history can show.
The user accounts are the one exception the Forgejo binary forces: its
`admin user` commands take the password only as an argument.

The provisioning token carries all scopes. It creates organisations,
repositories, teams and protected tags, and administers accounts. It is never
used to act for a person; that is the person's own OAuth token.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bootstrap.compose import Compose

PROVISIONING_TOKEN_NAME = "unicon-provisioning"
CONTAINER_USER = "git"
API_INSIDE_THE_CONTAINER = "http://localhost:3000/api/v1"


class ForgejoError(Exception):
    """Forgejo refused something bootstrap needs."""


@dataclass(frozen=True)
class OAuthApplication:
    client_id: str
    client_secret: str


class Forgejo:
    def __init__(self, compose: Compose, service: str) -> None:
        self._compose = compose
        self._service = service

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

    def token_is_valid(self, token: str) -> bool:
        if not token:
            return False
        status, _ = self.api("GET", "/user", token=token)
        return status == 200

    def mint_access_token(self, username: str, password: str) -> str:
        """Replace the provisioning token and return the new one.

        Forgejo shows a token once. If .env no longer has a working one there is
        nothing to recover, so the old token is deleted and a new one minted
        rather than the run failing.
        """
        auth = (username, password)
        for token in _ok(self.api("GET", f"/users/{username}/tokens", auth=auth)):
            if token["name"] == PROVISIONING_TOKEN_NAME:
                _ok(
                    self.api(
                        "DELETE", f"/users/{username}/tokens/{token['id']}", auth=auth
                    )
                )
        created = _ok(
            self.api(
                "POST",
                f"/users/{username}/tokens",
                auth=auth,
                body={"name": PROVISIONING_TOKEN_NAME, "scopes": ["all"]},
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
        auth = (owner, owner_password)
        for application in _ok(self.api("GET", "/user/applications/oauth2", auth=auth)):
            if application["name"] != name:
                continue
            if (
                known is not None
                and known.client_id == application["client_id"]
                and redirect_uri in application["redirect_uris"]
            ):
                return known, False
            _ok(
                self.api(
                    "DELETE",
                    f"/user/applications/oauth2/{application['id']}",
                    auth=auth,
                )
            )
        created = _ok(
            self.api(
                "POST",
                "/user/applications/oauth2",
                auth=auth,
                body={
                    "name": name,
                    "redirect_uris": [redirect_uri],
                    "confidential_client": True,
                },
            )
        )
        return OAuthApplication(created["client_id"], created["client_secret"]), True

    def api(
        self,
        method: str,
        path: str,
        *,
        auth: tuple[str, str] | None = None,
        token: str | None = None,
        body: Any = None,
    ) -> tuple[int, Any]:
        """One API call from inside the container. Returns the status and the
        decoded body, or None for an empty one.
        """
        output = self._compose.execute(
            self._service,
            "curl",
            "--config",
            "-",
            stdin=curl_config(method, path, auth=auth, token=token, body=body),
        )
        text, _, status = output.rpartition("\n")
        try:
            code = int(status)
        except ValueError as failure:
            raise ForgejoError(f"{method} {path}: curl printed no status") from failure
        return code, json.loads(text) if text.strip() else None


def curl_config(
    method: str,
    path: str,
    *,
    auth: tuple[str, str] | None,
    token: str | None,
    body: Any,
) -> str:
    """The curl configuration for one API call, every value quoted the way
    curl's configuration file wants it. Written to curl's standard input.
    """
    lines = [
        f"url = {_quoted(API_INSIDE_THE_CONTAINER + path)}",
        f"request = {_quoted(method)}",
        "silent",
        "show-error",
        'header = "Accept: application/json"',
        'write-out = "\\n%{http_code}"',
    ]
    if auth is not None:
        lines.append(f"user = {_quoted(f'{auth[0]}:{auth[1]}')}")
    if token is not None:
        lines.append(f"header = {_quoted(f'Authorization: token {token}')}")
    if body is not None:
        lines.append('header = "Content-Type: application/json"')
        lines.append(f"data = {_quoted(json.dumps(body))}")
    return "\n".join(lines) + "\n"


def _quoted(value: str) -> str:
    """A value in curl's double-quoted form: backslash and the quote escaped,
    line breaks written as escapes, so any character is carried.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _ok(answer: tuple[int, Any]) -> Any:
    status, body = answer
    if status >= 400:
        raise ForgejoError(f"Forgejo answered {status}: {str(body)[:200]}")
    return body
