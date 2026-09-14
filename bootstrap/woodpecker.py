"""Minting the Woodpecker API token for unicon-ci.

Woodpecker has no way to create a token except through its web UI, and the three
steps this does are all forced (stack-test-findings.md 3.4):

1. Woodpecker only knows people who signed in through the forge, so the script
   has to complete a real Forgejo OAuth round trip as unicon-ci.
2. A Woodpecker web session cannot make API writes. Every POST with only the
   session cookie returns 401.
3. The endpoint that mints a token is itself an API write, so it needs the CSRF
   token that Woodpecker hands its own single-page app in /web-config.js.

Minting adds a token rather than replacing one: every token Woodpecker has ever
issued this account stays valid until `DELETE /api/user/token` rotates the
account's hash and invalidates all of them at once. So a token .env already
holds is checked first and kept, and a run that cannot see a working one mints
another instead of leaving a trail of live credentials behind it.
"""

from __future__ import annotations

from urllib.parse import urljoin

import httpx

from bootstrap.htmlform import hidden_inputs

_MAX_REDIRECTS = 12


class WoodpeckerError(Exception):
    """The token dance did not end with a usable token."""


class Woodpecker:
    def __init__(self, public_url: str, forgejo_public_url: str) -> None:
        self._url = public_url.rstrip("/")
        self._forgejo_url = forgejo_public_url.rstrip("/")

    def is_answering(self) -> bool:
        # Woodpecker answers /healthz with 204, not 200.
        return httpx.get(f"{self._url}/healthz", timeout=10.0).status_code < 400

    def token_is_valid(self, token: str) -> bool:
        if not token:
            return False
        response = httpx.get(
            f"{self._url}/api/user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        return response.status_code == 200

    def mint_token(self, username: str, password: str) -> str:
        with httpx.Client(follow_redirects=False, timeout=60.0) as browser:
            self._sign_into_forgejo(browser, username, password)
            self._sign_into_woodpecker(browser)
            return self._mint(browser)

    def _sign_into_forgejo(
        self, browser: httpx.Client, username: str, password: str
    ) -> None:
        """Sign in the way the browser form does.

        Forgejo 15.0.8 puts no _csrf field on this form and accepts the post
        without one; it relies on SameSite cookies instead. Any hidden field
        that is there is sent back anyway, so an older Forgejo still works.
        """
        page = browser.get(f"{self._forgejo_url}/user/login")
        fields = hidden_inputs(page.text)
        fields.update({"user_name": username, "password": password})
        response = browser.post(
            f"{self._forgejo_url}/user/login",
            data=fields,
            headers={"Referer": f"{self._forgejo_url}/user/login"},
        )
        if response.status_code not in (302, 303):
            raise WoodpeckerError(
                f"Forgejo sign-in as {username} returned {response.status_code}"
            )

    def _sign_into_woodpecker(self, browser: httpx.Client) -> None:
        """Walk the OAuth round trip by hand, approving Unicon on the way."""
        location = f"{self._url}/authorize"
        for _ in range(_MAX_REDIRECTS):
            response = browser.get(location)
            if response.status_code in (301, 302, 303, 307, 308):
                location = urljoin(location, response.headers["location"])
                continue
            if response.status_code != 200:
                raise WoodpeckerError(
                    f"{location} returned {response.status_code} during sign-in"
                )
            if browser.cookies.get("user_sess"):
                return
            location = self._approve_consent(browser, response)
        raise WoodpeckerError("the OAuth round trip did not settle")

    def _approve_consent(self, browser: httpx.Client, page: httpx.Response) -> str:
        """Post Forgejo's consent form. Returns where it sends us next.

        The approve control is a button, not an input, so granted=true has to be
        added by hand; without it Forgejo reads the post as a refusal and
        answers error=access_denied.
        """
        fields = hidden_inputs(page.text)
        if "client_id" not in fields:
            raise WoodpeckerError(f"{page.url} is not the consent page")
        fields["granted"] = "true"
        response = browser.post(
            f"{self._forgejo_url}/login/oauth/grant",
            data=fields,
            headers={"Referer": str(page.url)},
        )
        location = response.headers.get("location")
        if not location:
            raise WoodpeckerError(
                f"consent returned {response.status_code}, no redirect"
            )
        return str(urljoin(str(page.url), location))

    def _mint(self, browser: httpx.Client) -> str:
        configuration = browser.get(f"{self._url}/web-config.js").text
        csrf = _javascript_string(configuration, "WOODPECKER_CSRF")
        if not csrf:
            raise WoodpeckerError("no WOODPECKER_CSRF in /web-config.js")
        response = browser.post(
            f"{self._url}/api/user/token", headers={"X-CSRF-TOKEN": csrf}
        )
        token = response.text.strip()
        if response.status_code != 200 or not token or token.startswith("<"):
            raise WoodpeckerError(
                f"POST /api/user/token returned {response.status_code}"
            )
        if not self.token_is_valid(token):
            raise WoodpeckerError("the minted token does not authorise an API call")
        return token


def _javascript_string(source: str, name: str) -> str | None:
    """Read `name = "value"` out of the served configuration script."""
    marker = f"{name} ="
    start = source.find(marker)
    if start < 0:
        return None
    opening = source.find('"', start)
    closing = source.find('"', opening + 1)
    if opening < 0 or closing < 0:
        return None
    return source[opening + 1 : closing]
