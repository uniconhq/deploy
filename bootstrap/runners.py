"""The development stack's runners: compose.dev.yaml runs one `unicon-base`
runner and one `unicon-exec` runner on this machine's Docker. Bootstrap
registers each at Forgejo as an instance runner and writes its id and token
to .env, and writes the labels each takes, every one naming the step helper
by its digest, since a job runs in the image its runner's label names. The
exec runner takes one label per slot size in machine/slots.json, so a job
of any slot lands on it.
"""

from __future__ import annotations

import json
from pathlib import Path

from bootstrap.forgejo import Forgejo, ForgejoError
from bootstrap.images import is_by_digest

DEV_RUNNERS = ("base", "exec")
RUNNERS_PATH = "/admin/actions/runners"


def labels(helper: str, slots: Path) -> dict[str, str]:
    """DEV_RUNNER_BASE_LABELS and DEV_RUNNER_EXEC_LABELS, each a JSON list
    the runner's config takes as it is."""
    if not is_by_digest(helper):
        raise ValueError(f"the step helper {helper} is not named by its digest")
    image = f"docker://{helper}"
    sizes = json.loads(slots.read_text())
    return {
        "DEV_RUNNER_BASE_LABELS": json.dumps([f"unicon-base:{image}"]),
        "DEV_RUNNER_EXEC_LABELS": json.dumps(
            [f"unicon-exec-{size}:{image}" for size in sizes]
        ),
    }


def registered(forgejo: Forgejo, token: str) -> set[str]:
    """The id of every instance runner Forgejo knows."""
    status, body = forgejo.api("GET", RUNNERS_PATH, token=token)
    if status != 200:
        raise ForgejoError(f"listing the runners: Forgejo answered {status}")
    return {str(runner["uuid"]) for runner in body}


def register(forgejo: Forgejo, token: str, name: str) -> tuple[str, str]:
    """Register an instance runner named `name`: its id and its token."""
    status, body = forgejo.api("POST", RUNNERS_PATH, token=token, body={"name": name})
    if status != 201:
        raise ForgejoError(f"registering the runner {name}: Forgejo answered {status}")
    return str(body["uuid"]), str(body["token"])
