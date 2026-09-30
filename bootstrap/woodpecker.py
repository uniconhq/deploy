"""Minting the Woodpecker API token for unicon-ci, and enrolling the dev agent.

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

With that token, which is an administrator's, it also enrols the development
agent: `POST /api/agents` makes a global agent and answers with its token,
which is what the agent connects with. Unlike the API token, an agent's token
can be read back, so a run that finds the agent by name reuses it, the one
whose token .env already holds when there are several.

An agent row outlives the machine behind it, and one that declares the label
grading runs ask for takes them again the moment anything connects with its
token. So after enrolling, every other agent that declares that label or
carries the development agent's name, and has not been heard from for an
hour, is deleted. One still reporting is left and named, since something is
running it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from bootstrap.htmlform import hidden_inputs

_MAX_REDIRECTS = 12
_AGENT_PAGE = 50
SILENT_AGENT_SECONDS = 3600


class WoodpeckerError(Exception):
    """The token dance did not end with a usable token."""


@dataclass(frozen=True)
class OtherAgents:
    """What `remove_other_agents` found besides the kept agent: the names of
    the silent ones it deleted, of the ones still reporting it left, and of
    the silent ones the CI would not delete.
    """

    removed: tuple[str, ...]
    reporting: tuple[str, ...]
    refused: tuple[str, ...]


class Woodpecker:
    def __init__(self, public_url: str, forgejo_public_url: str) -> None:
        self._url = public_url.rstrip("/")
        self._forgejo_url = forgejo_public_url.rstrip("/")

    def is_answering(self) -> bool:
        """Whether /healthz answers. Woodpecker answers it with 204, not 200."""
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

    def agents(self, token: str) -> list[dict[str, Any]]:
        """Every agent the CI knows, global and per org, tokens included."""
        headers = {"Authorization": f"Bearer {token}"}
        found: list[dict[str, Any]] = []
        page = 1
        while True:
            response = httpx.get(
                f"{self._url}/api/agents",
                params={"page": page, "perPage": _AGENT_PAGE},
                headers=headers,
                timeout=30.0,
            )
            if response.status_code != 200:
                raise WoodpeckerError(
                    f"GET /api/agents returned {response.status_code}"
                )
            listed = response.json() or []
            found += listed
            if len(listed) < _AGENT_PAGE:
                return found
            page += 1

    def ensure_agent(self, token: str, name: str, known: str = "") -> tuple[str, bool]:
        """The token of the global agent `name`, and whether this run
        created the agent.

        An agent the server knows by that name is kept, and its token read
        back: Woodpecker returns an agent's token to an administrator, so a
        lost .env costs nothing here. Of several by that name, the one whose
        token is `known`, the one .env holds, is kept. A new one is made as a
        global agent, which takes work from every org, the way a platform
        machine does.
        """
        named = [
            agent
            for agent in self.agents(token)
            if agent.get("name") == name and agent.get("token")
        ]
        for agent in named:
            if known and agent["token"] == known:
                return str(agent["token"]), False
        if named:
            return str(named[0]["token"]), False
        response = httpx.post(
            f"{self._url}/api/agents",
            json={"name": name, "no_schedule": False},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        created = response.json() if response.status_code in (200, 201) else {}
        if not created.get("token"):
            raise WoodpeckerError(f"POST /api/agents returned {response.status_code}")
        return str(created["token"]), True

    def remove_other_agents(
        self,
        token: str,
        kept: str,
        name: str,
        label: tuple[str, str],
        now: float | None = None,
    ) -> OtherAgents:
        """Delete every agent but the one holding the token `kept` that
        carries the name `name` or declares the label `label`, and has not
        been heard from for `SILENT_AGENT_SECONDS`: a leftover of an earlier
        run or test that would take grading runs again the moment anything
        connected with its token. An agent never heard from counts from when
        it was made.
        """
        clock = time.time() if now is None else now
        key, value = label
        removed: list[str] = []
        reporting: list[str] = []
        refused: list[str] = []
        for agent in self.agents(token):
            labels = agent.get("custom_labels") or {}
            if agent.get("token") == kept:
                continue
            if agent.get("name") != name and labels.get(key) != value:
                continue
            heard = max(
                int(agent.get("last_contact") or 0), int(agent.get("created") or 0)
            )
            if clock - heard < SILENT_AGENT_SECONDS:
                reporting.append(str(agent.get("name")))
                continue
            response = httpx.delete(
                f"{self._url}/api/agents/{agent['id']}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            if response.status_code < 300:
                removed.append(str(agent.get("name")))
            else:
                refused.append(str(agent.get("name")))
        return OtherAgents(tuple(removed), tuple(reporting), tuple(refused))

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
