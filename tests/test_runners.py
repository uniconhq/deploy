"""The development stack's runners: the labels bootstrap writes for them, each
naming the step helper by its digest, and their registration at Forgejo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bootstrap import runners

DEPLOY = Path(__file__).resolve().parent.parent
HELPER = "localhost:5000/uniconhq/step-helper@sha256:" + "a" * 64


def test_the_exec_runner_takes_every_slot_size_and_base_its_own_label() -> None:
    written = runners.labels(HELPER, DEPLOY / "machine" / "slots.json")

    assert json.loads(written["DEV_RUNNER_BASE_LABELS"]) == [
        f"unicon-base:docker://{HELPER}"
    ]
    assert json.loads(written["DEV_RUNNER_EXEC_LABELS"]) == [
        f"unicon-exec-small:docker://{HELPER}",
        f"unicon-exec-big:docker://{HELPER}",
    ]


def test_a_helper_not_named_by_its_digest_is_refused() -> None:
    with pytest.raises(ValueError, match="not named by its digest"):
        runners.labels(
            "localhost:5000/uniconhq/step-helper:dev",
            DEPLOY / "machine" / "slots.json",
        )


class _Forgejo:
    def __init__(self) -> None:
        self.runners: list[dict[str, str]] = []

    def api(
        self, method: str, path: str, *, token: str, body: object = None
    ) -> tuple[int, object]:
        assert path == runners.RUNNERS_PATH
        if method == "GET":
            return 200, self.runners
        assert isinstance(body, dict)
        made = {"uuid": f"id-{len(self.runners)}", "token": "t", "name": body["name"]}
        self.runners.append(made)
        return 201, made


def test_a_registered_runner_is_listed() -> None:
    forgejo = _Forgejo()

    made = runners.register(forgejo, "admin", "dev-exec")  # type: ignore[arg-type]

    assert made == ("id-0", "t")
    assert runners.registered(forgejo, "admin") == {"id-0"}  # type: ignore[arg-type]
