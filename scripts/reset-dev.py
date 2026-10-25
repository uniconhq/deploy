"""Reset the development stack to a known state: empty but for two people and
one contest to try things on.

What it leaves, every time:

- `alice` and `bob`, Forgejo accounts with the passwords and email addresses
  in `.env` (`DEV_ALICE_PASSWORD`, `DEV_ALICE_EMAIL`, `DEV_BOB_PASSWORD`,
  `DEV_BOB_EMAIL`), each signed in to the app once so the app knows them;
- the org `demo`, made by alice, who is its admin;
- its contest `sample`, "Sample contest", running from an hour ago for 30
  days, open to anyone who signs up, each sign-up approved by an organiser
  (a new contest's own registration);
- its task `sum`, on unicon/classic@v3: A + B in Python, its statement,
  three tests and a submission rate of 10 in any 60 seconds, saved in one
  save before the contest opens, which waits until that save is published
  so a submission grades.

Bob has not registered, so signing up as him and approving him as alice is
the first thing there is to try.

It throws the stack's data away (`down -v`: the database, Forgejo, the object
store, Woodpecker), bootstraps an empty stack and starts both profiles, with
the images already built: `--build` rebuilds the backend and the frontend
from their checkouts first. The grading images stay in the local registry,
outside the stack. The four keys are kept in `.env` by hand; bootstrap keeps
a key it does not know at the end of the file. Then it makes the accounts with
Forgejo's own command, signs each in through Forgejo's pages, and makes the
rest through the API as alice, with the same steps the end-to-end tests of a
task take (`tests/e2e/api.py`), so a change there is mended once for both.
Run it from the deploy directory, once `uv run playwright install chromium`
has been run:

    uv run scripts/reset-dev.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
os.environ.setdefault("UNICON_E2E_URL", "http://localhost:8080")

from bootstrap import envfile  # noqa: E402
from bootstrap.compose import Compose  # noqa: E402
from bootstrap.forgejo import Forgejo  # noqa: E402
from bootstrap.main import main as bootstrap  # noqa: E402

FILES = ("compose.yaml", "compose.dev.yaml")
PROFILES = ("--profile", "app", "--profile", "agent")
APP_SERVICES = ("backend-migrate", "backend", "frontend")
PEOPLE = ("alice", "bob")
ORG = "demo"
CONTEST = "sample"
CONTEST_NAME = "Sample contest"
TASK = "sum"
STATEMENT = """\
# A + B

Read two integers *a* and *b*, and print their sum.

## Input

One line with *a* and *b*, separated by a space, each between -1000000000
and 1000000000.

## Output

One line with *a* + *b*.

## Example

Input:

    1 2

Output:

    3
"""
TESTS = (("1 2\n", "3\n"), ("10 32\n", "42\n"), ("-7 7\n", "0\n"))
"""Each test's input and answer."""
TASK_YAML = """\
name: A + B
workflow: unicon/classic@v3
inputs:
  code: {entrant: {label: Your solution, max_size: 1MB}}
  language: python
  time_limit: 2
  memory_limit: 256
repeat:
  - inputs: [input, answer]
    tuples:
      - [tests/1.in, tests/1.ans]
      - [tests/2.in, tests/2.ans]
      - [tests/3.in, tests/3.ans]
scoring:
  values:
    passed: {block: sum, over: {runs: all}, of: {run: [all, passed]}}
  show:
    now: [passed, all, tests/]
submissions: {rate: {count: 10, per: 60}}
"""
WHEN = "%Y-%m-%dT%H:%M:%SZ"
READY_TIMEOUT_S = 300


class ResetFailed(Exception):
    """A step of the reset did not succeed."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset the development stack to alice, bob and a sample contest."
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="rebuild the backend and frontend images from their checkouts",
    )
    options = parser.parse_args(argv)
    try:
        people = accounts(envfile.load(DEPLOY / ".env"))
        compose = Compose(DEPLOY, FILES)
        step("throwing the stack's data away")
        docker_compose(*PROFILES, "down", "--volumes", "--remove-orphans")
        step("bootstrapping an empty stack")
        if bootstrap(["--directory", str(DEPLOY)]) != 0:
            raise ResetFailed("bootstrap did not finish; its output says why")
        step("starting the app and the grading machine")
        build = ["--build"] if options.build else []
        docker_compose("--profile", "app", "up", "-d", *build, *APP_SERVICES)
        docker_compose("--profile", "agent", "up", "-d")
        wait_until_ready(compose)
        step("making alice and bob")
        forgejo = Forgejo(compose, "forgejo")
        for name, (password, email) in people.items():
            forgejo.ensure_user(name, password, email, admin=False)
        step("making the sample contest as alice")
        seed(people)
    except ResetFailed as failure:
        print(f"reset failed: {failure}", file=sys.stderr)
        return 1
    app = os.environ["UNICON_E2E_URL"]
    print(
        f"\nReady at {app}: alice and bob, and {CONTEST_NAME} at "
        f"{app}/contests/{ORG}/{CONTEST}, which bob has not registered for.\n"
        "Sign in from a new tab on the app: a page open from before the reset "
        "holds the app's old client id at Forgejo, which no longer exists."
    )
    return 0


def accounts(values: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Each person's password and email address from `.env`, refusing before
    anything is thrown away when one is missing.
    """
    found: dict[str, tuple[str, str]] = {}
    missing: list[str] = []
    for name in PEOPLE:
        keys = (f"DEV_{name.upper()}_PASSWORD", f"DEV_{name.upper()}_EMAIL")
        missing += [key for key in keys if not values.get(key)]
        found[name] = (values.get(keys[0], ""), values.get(keys[1], ""))
    if missing:
        raise ResetFailed(f".env has no {', '.join(missing)}; nothing was changed")
    return found


def docker_compose(*arguments: str) -> None:
    """A compose command on the dev files, its output shown as it goes."""
    command = ["docker", "compose"]
    for file in FILES:
        command += ["-f", file]
    if subprocess.run([*command, *arguments], cwd=DEPLOY).returncode != 0:
        raise ResetFailed(f"docker compose {' '.join(arguments)} did not succeed")


def wait_until_ready(compose: Compose) -> None:
    """Until the backend and the frontend are healthy and the proxy serves the
    app's first page.
    """
    app = os.environ["UNICON_E2E_URL"]
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if compose.is_healthy("backend") and compose.is_healthy("frontend"):
            try:
                if httpx.get(app, timeout=5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
        time.sleep(3)
    raise ResetFailed(f"the app at {app} was not ready in {READY_TIMEOUT_S} s")


def seed(people: dict[str, tuple[str, str]]) -> None:
    """Sign both in, and make the org, the contest and its task as alice."""
    from playwright.sync_api import Page, sync_playwright

    from tests.e2e import api, stack

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            pages: dict[str, Page] = {}
            for name, (password, _) in people.items():
                page = browser.new_context(
                    viewport={"width": 1280, "height": 900}
                ).new_page()
                page.set_default_timeout(30_000)
                stack.sign_in(page, name, password)
                pages[name] = page
            alice = api.Caller.of(pages["alice"])
            alice.send("POST", "/orgs", {"name": ORG})
            contest = f"/orgs/{ORG}/contests/{CONTEST}"
            alice.send("POST", f"/orgs/{ORG}/contests", {"name": CONTEST})
            alice.send("POST", f"{contest}/tasks", {"name": TASK})
            now = datetime.now(UTC).replace(second=0, microsecond=0)
            api.write_contest(alice, contest, contest_yaml("draft", now))
            api.save_task(alice, f"{contest}/tasks/{TASK}", task_files())
            api.open_once_published(alice, contest, contest_yaml("published", now))
        finally:
            browser.close()


def task_files() -> dict[str, str]:
    """The task's files: its settings, its statement and its tests."""
    files = {"task.yaml": TASK_YAML, "statement.md": STATEMENT}
    for number, (given, answer) in enumerate(TESTS, start=1):
        files[f"tests/{number}.in"] = given
        files[f"tests/{number}.ans"] = answer
    return files


def contest_yaml(state: str, now: datetime) -> str:
    """The contest, running from an hour before `now` for 30 days, seen by
    anyone signed in, its registration the one a new contest starts with:
    anyone may register, and an organiser approves each."""
    return (
        f"name: {CONTEST_NAME}\n"
        f"start: {(now - timedelta(hours=1)).strftime(WHEN)}\n"
        f"end: {(now + timedelta(days=30)).strftime(WHEN)}\n"
        f"state: {state}\nvisibility: signed-in\ntasks:\n  - id: {TASK}\n"
    )


def step(what: str) -> None:
    print(f"\n== {what}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
