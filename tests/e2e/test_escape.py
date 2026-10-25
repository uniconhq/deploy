"""Grade the runner's escape fixtures (`runner/escape/`) on the running
compose stack, through a pipeline the forge builds, on a grading machine.

An organiser, made with the operator's command, makes an org, a contest
and the task `escape` through the API, saves the fixtures' workflow as the
org's `escape@v1` and the task's files on it, and opens the contest. An
entrant registers, is approved, and submits `escape.c` as their code. Each
run is one attack: it must end on a verdict `expected.json` gives it, and
an accepted run is one where no check escaped but those the task's
`check/allowed` lets a box pass.

A machine's own settings decide some attacks: Docker Desktop's VM lets an
unprivileged process make a BPF map, which the grading machine's self-test
refuses. `UNICON_E2E_ESCAPE_ALLOW` adds such checks to `allowed`, comma
separated, so a run on a machine the self-test would refuse says what else
escaped. The test needs a grading machine with exec's shape,
which the CI stack has none of, so it is skipped unless `UNICON_E2E_ESCAPE`
is set, besides `UNICON_E2E_URL`. The fixtures come from the runner
checkout beside this repository, or `UNICON_E2E_RUNNER`.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from tests.e2e.api import (
    Caller,
    contest_yaml,
    graded,
    let_in,
    open_once_published,
    save_task,
    submit,
    write_contest,
)
from tests.e2e.stack import DEPLOY, needs_stack

RUNNER = Path(os.environ.get("UNICON_E2E_RUNNER") or DEPLOY.parent / "runner")
ESCAPE = RUNNER / "escape"

pytestmark = [
    needs_stack,
    pytest.mark.skipif(
        not os.environ.get("UNICON_E2E_ESCAPE"),
        reason="UNICON_E2E_ESCAPE says no grading machine with exec's shape is up",
    ),
]


def task_files(org: str) -> dict[str, str]:
    """The task's files as the fixtures hold them, on the org's workflow,
    with the checks `UNICON_E2E_ESCAPE_ALLOW` adds."""
    task = ESCAPE / "task"
    files = {
        str(path.relative_to(task)): path.read_text()
        for path in sorted(task.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    files["task.yaml"] = files["task.yaml"].replace("escape-org/", f"{org}/")
    added = [
        name
        for name in os.environ.get("UNICON_E2E_ESCAPE_ALLOW", "").split(",")
        if name
    ]
    files["check/allowed"] += "".join(f"{name}\n" for name in added)
    files["statement.md"] = "# Escape\n\nEvery attack is refused.\n"
    return files


@pytest.mark.skipif(not ESCAPE.is_dir(), reason="no runner checkout with escape/")
def test_every_attack_from_a_box_is_refused(browser: Browser) -> None:
    suffix = secrets.token_hex(3)
    org = f"esc{suffix}"
    organiser = Caller(browser, f"escorg{suffix}")
    entrant = Caller(browser, f"escent{suffix}")

    organiser.send("POST", "/orgs", {"name": org})
    contest = f"/orgs/{org}/contests/escape"
    organiser.send("POST", f"/orgs/{org}/contests", {"name": "escape"})
    organiser.send("POST", f"{contest}/tasks", {"name": "escape"})
    workflow = f"/workflows/{org}/escape"
    organiser.send("POST", "/workflows", {"owner": org, "name": "escape"})
    token = (organiser.get(workflow).get("draft") or {}).get("token")
    content = (ESCAPE / "workflow.yaml").read_text()
    organiser.send("PUT", f"{workflow}/draft", {"content": content, "token": token})
    organiser.send("POST", f"{workflow}/versions", {"version": "v1"})
    write_contest(organiser, contest, contest_yaml("Escape", "escape", "draft"))
    task = f"{contest}/tasks/escape"
    save_task(organiser, task, task_files(org))
    open_once_published(
        organiser, contest, contest_yaml("Escape", "escape", "published")
    )
    let_in(organiser, entrant, contest)

    code = (ESCAPE / "escape.c").read_bytes()
    grading = graded(entrant, task, submit(entrant, task, "escape.c", code))

    expected = json.loads((ESCAPE / "expected.json").read_text())
    ended = {
        Path(run["tuples"][0]["key"]["attack"]).stem: run["verdict"]
        for run in grading["runs"]
    }
    assert set(ended) == set(expected)
    wrong = {
        name: verdict
        for name, verdict in ended.items()
        if verdict not in expected[name]
    }
    assert wrong == {}, f"attacks not refused: {wrong}"
