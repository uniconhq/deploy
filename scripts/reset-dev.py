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
- its task `sum`, the starter task on unicon/classic@v2, with two tests more
  than the starter's one, saved and published so it grades.

Bob has not registered, so signing up as him and approving him as alice is
the first thing there is to try.

It throws the stack's data away (`down -v`: the database, Forgejo, the object
store, Woodpecker), bootstraps an empty stack and starts both profiles, with
the images already built: `--build` rebuilds the backend and the frontend
from their checkouts first. The grading images stay in the local registry,
outside the stack. The four keys are kept in `.env` by hand; bootstrap keeps
a key it does not know at the end of the file. Then it makes the accounts with
Forgejo's own command and the rest from the app's pages, with the same steps
the browser tests take (`tests/e2e/stack.py`), so a page that changes is
mended there once for both. Run it from the deploy directory, once
`uv run playwright install chromium` has been run:

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
EXTRA_TESTS = (
    {"input": b"10 32\n", "answer": b"42\n"},
    {"input": b"-7 7\n", "answer": b"0\n"},
)
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

    from tests.e2e import stack

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
            alice = pages["alice"]
            stack.create_org(alice, ORG)
            stack.create(alice, CONTEST, "Create contest", "New contest")
            stack.create(alice, TASK, "Create task", "New task")
            for files in EXTRA_TESTS:
                stack.add_test(alice, "main", dict(files))
            stack.submission_rate(alice, 10, 60)
            stack.save_task(alice)
            now = datetime.now(UTC).replace(second=0, microsecond=0)
            stack.contest_settings(
                alice,
                ORG,
                CONTEST,
                Name=CONTEST_NAME,
                Start=stack.local(now - timedelta(hours=1)),
                End=stack.local(now + timedelta(days=30)),
            )
            stack.open_contest(alice, ORG, CONTEST)
        finally:
            browser.close()


def step(what: str) -> None:
    print(f"\n== {what}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
