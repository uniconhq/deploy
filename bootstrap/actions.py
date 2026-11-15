"""The action copies (Decision 20). Every job forge writes uses
`actions/checkout`, `forgejo/upload-artifact` and `forgejo/download-artifact`
at `main`, and Forgejo reads them from its own orgs `actions` and `forgejo`
(`DEFAULT_ACTIONS_URL = self` in forgejo/app.ini): plain public copies with
Actions off, made before anyone can sign up, written by the platform alone
and never deleted.

PINS is the one place the pins live. A copy whose `main` is not at its pin
has the pin pushed there, history and all, by Forgejo's own git from inside
its container (Forgejo.push_commit).
"""

from __future__ import annotations

from collections.abc import Iterator

from bootstrap.forgejo import Forgejo
from bootstrap.platform_repos import PlatformRepos

PINS = {
    # v4.3.1
    "actions/checkout": (
        "https://github.com/actions/checkout.git",
        "34e114876b0b11c390a56381ad16ebd13914f8d5",
    ),
    # v4
    "forgejo/upload-artifact": (
        "https://code.forgejo.org/forgejo/upload-artifact.git",
        "16871d9e8cfcf27ff31822cac382bbb5450f1e1e",
    ),
    # v4
    "forgejo/download-artifact": (
        "https://code.forgejo.org/forgejo/download-artifact.git",
        "d8d0a99033603453ad2255e58720b460a0555e1e",
    ),
}


def copy_actions(
    forgejo: Forgejo, repos: PlatformRepos, account: tuple[str, str]
) -> Iterator[tuple[str, bool]]:
    """Each copy, and whether this run moved its `main` to the pin.
    `account` is the platform account and its token, which pushes."""
    for copy, (upstream, pin) in PINS.items():
        owner, name = copy.split("/")
        repos.ensure_org(owner, f"Copies of the {owner}/* actions jobs use")
        repos.ensure_repository(owner, name)
        repos.ensure_actions_off(owner, name)
        if repos.head(owner, name) == pin:
            yield copy, False
            continue
        forgejo.push_commit(upstream, pin, copy, account)
        yield copy, True
