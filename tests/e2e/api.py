"""What the end-to-end tests that drive a run task through the API do the
same way: a person's requests, signed in through Forgejo, a contest that
opens once its task's save is published, an entrant let in, a submission
of one uploaded file, and its grading once it has finished.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from playwright.sync_api import APIRequestContext, APIResponse, Browser, Page

from tests.e2e.stack import APP, VERDICT_TIMEOUT_MS, create_account, sign_in

API = f"{APP}/api/v1"
ORIGIN = {"Origin": APP}
WHEN = "%Y-%m-%dT%H:%M:%SZ"


def answer(response: APIResponse, what: str) -> Any:
    assert response.ok, f"{what}: {response.status} {response.text()[:2000]}"
    return response.json() if response.text() else None


class Caller:
    """One person's requests to the API, signed in through Forgejo."""

    def __init__(self, browser: Browser, username: str) -> None:
        context = browser.new_context()
        sign_in(context.new_page(), username, create_account(username))
        self.request: APIRequestContext = context.request

    @classmethod
    def of(cls, page: Page) -> Caller:
        """The person `page` is already signed in as."""
        caller = cls.__new__(cls)
        caller.request = page.context.request
        return caller

    def get(self, path: str) -> Any:
        return answer(self.request.get(API + path), f"GET {path}")

    def send(self, method: str, path: str, body: object) -> Any:
        sent = self.request.fetch(API + path, method=method, data=body, headers=ORIGIN)
        return answer(sent, f"{method} {path}")


def token_of(caller: Caller, path: str) -> str | None:
    """The token a save of the file at `path` carries, none for a new one."""
    found = caller.request.get(API + path)
    return found.json().get("token") if found.ok else None


def contest_yaml(name: str, task: str, state: str) -> str:
    """A contest named `name` running now, with the one task `task`."""
    now = datetime.now(UTC)
    return (
        f"name: {name}\n"
        f"start: {(now - timedelta(minutes=1)).strftime(WHEN)}\n"
        f"end: {(now + timedelta(hours=6)).strftime(WHEN)}\n"
        f"state: {state}\nvisibility: everyone\ntasks:\n  - id: {task}\n"
    )


def write_contest(organiser: Caller, contest: str, text: str) -> None:
    path = f"{contest}/files/contest.yaml"
    token = organiser.get(path).get("token")
    organiser.send("PUT", path, {"content": text, "token": token, "confirm": True})


def save_task(organiser: Caller, task: str, files: dict[str, str]) -> None:
    """Save every file of `files` to the task in one save, its first."""
    changes = [
        {
            "path": path,
            "content": text,
            "token": token_of(organiser, f"{task}/files/{path}"),
        }
        for path, text in files.items()
    ]
    saved = organiser.send(
        "POST", f"{task}/save", {"changes": changes, "confirm": True, "message": "Task"}
    )
    if saved.get("status") == "preparing":
        deadline = time.monotonic() + 16 * 60
        while True:
            state = organiser.get(f"{task}/preparation")
            assert state["status"] not in ("refused", "replaced"), state.get("reason")
            if state["status"] == "ready":
                saved = state["result"]
                break
            assert time.monotonic() < deadline, (
                "the task's packages were not prepared in time"
            )
            time.sleep(1)
    assert saved.get("number") == 1, saved


def open_once_published(organiser: Caller, contest: str, text: str) -> None:
    """Write the contest as `text`, its state published, once the task's
    save, checked by a pipeline of its own, has published the task."""
    path = f"{contest}/files/contest.yaml"
    deadline = time.monotonic() + VERDICT_TIMEOUT_MS / 1000
    while True:
        token = organiser.get(path).get("token")
        body = {"content": text, "token": token, "confirm": True}
        sent = organiser.request.fetch(
            API + path, method="PUT", data=body, headers=ORIGIN
        )
        if sent.ok or "not published yet" not in sent.text():
            answer(sent, f"PUT {path}")
            return
        assert time.monotonic() < deadline, "the task's save was not published in time"
        time.sleep(3)


def entrant_rows(organiser: Caller, contest: str) -> list[dict[str, Any]]:
    """The contest's entrants as its organisers' list gives them."""
    listed = organiser.get(f"{contest}/organise/entrants")
    rows = (
        listed
        if isinstance(listed, list)
        else listed.get("entrants", listed.get("items", []))
    )
    return list(rows)


def let_in(organiser: Caller, entrant: Caller, contest: str) -> None:
    """The entrant registers and the organiser approves whoever waits."""
    entrant.send("POST", f"{contest}/entrants", {})
    for row in entrant_rows(organiser, contest):
        if row.get("status") == "pending":
            organiser.send(
                "POST", f"{contest}/organise/entrants/{row['id']}/approve", {}
            )


def put_upload(caller: Caller, slot: dict[str, Any], content: bytes) -> None:
    """Send an upload's bytes to the address its slot gives, unless the
    store already holds them."""
    if slot["ready"]:
        return
    url = APP + slot["url"] if slot["url"].startswith("/") else slot["url"]
    headers = {**ORIGIN, "Content-Type": "application/octet-stream"}
    sent = caller.request.fetch(url, method="PUT", data=content, headers=headers)
    assert sent.ok, (sent.status, sent.text())


def submit(
    entrant: Caller,
    task: str,
    name: str,
    content: bytes,
    values: dict[str, str] | None = None,
) -> int:
    """Submit the one file `name` as the task's `code` folder, and `values`
    for the task's other entrant inputs; its number."""
    slot = entrant.send(
        "POST",
        f"{task}/uploads",
        {
            "input": "code",
            "filename": name,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    put_upload(entrant, slot, content)
    entrant.send("POST", f"{task}/uploads/{slot['id']}/complete", {})
    made = entrant.send(
        "POST",
        f"{task}/submissions",
        {
            "idempotency_key": uuid.uuid4().hex,
            "inputs": {
                "code": {"uploads": [slot["id"]]},
                **{key: {"value": value} for key, value in (values or {}).items()},
            },
        },
    )
    return int(made["number"])


def graded(entrant: Caller, task: str, number: int) -> dict[str, Any]:
    """Submission `number`'s grading, once it has finished."""
    deadline = time.monotonic() + VERDICT_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        grading = entrant.get(f"{task}/submissions/{number}")["grading"]
        if grading["status"] not in ("queued", "grading", "delayed"):
            return dict(grading)
        time.sleep(3)
    raise AssertionError(f"submission {number} was not graded in time")
