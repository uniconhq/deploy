"""The action copies: the orgs `actions` and `forgejo` made public, a public
copy of each action with Actions off and its `main` pushed to the pin, the credential on
standard input only, and a rerun with every copy at its pin pushing nothing.
"""

from __future__ import annotations

import base64

from bootstrap import actions
from bootstrap.forgejo import Forgejo
from bootstrap.platform_repos import PlatformRepos
from tests.forgejo_fake import FakeForgejo

TOKEN = "provisioning-token"
ACCOUNT = ("unicon-backend", TOKEN)


def _copy(forge: FakeForgejo) -> list[tuple[str, bool]]:
    forgejo = Forgejo(forge, "forgejo")
    return list(actions.copy_actions(forgejo, PlatformRepos(forgejo, TOKEN), ACCOUNT))


def test_each_action_is_copied_public_with_actions_off_and_main_at_the_pin() -> None:
    forge = FakeForgejo()

    assert _copy(forge) == [(copy, True) for copy in actions.PINS]

    assert forge.visibility == {"actions": "public", "forgejo": "public"}
    for copy, (_, pin) in actions.PINS.items():
        assert not forge.repos[copy].private
        assert not forge.repos[copy].has_actions
        assert forge.repos[copy].head == pin
    pushed = [arguments[-3:] for arguments, _ in forge.pushes]
    assert pushed == [
        (upstream, pin, f"http://localhost:3000/{copy}.git")
        for copy, (upstream, pin) in actions.PINS.items()
    ]
    basic = base64.b64encode(f"unicon-backend:{TOKEN}".encode()).decode()
    for arguments, stdin in forge.pushes:
        assert stdin == f"Authorization: Basic {basic}\n"
        assert not any(TOKEN in each or basic in each for each in arguments)


def test_a_rerun_pushes_only_a_copy_whose_main_moved() -> None:
    forge = FakeForgejo()
    _copy(forge)
    forge.repos["actions/checkout"].head = "0" * 40
    forge.pushes.clear()
    forge.requests.clear()

    assert _copy(forge) == [(copy, copy == "actions/checkout") for copy in actions.PINS]
    assert [arguments[-2] for arguments, _ in forge.pushes] == [
        actions.PINS["actions/checkout"][1]
    ]
    assert forge.writes() == []
