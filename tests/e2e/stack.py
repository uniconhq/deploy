"""What every end-to-end test on the running stack does the same way:
making people with the operator's command, signing them in through
Forgejo's pages, and driving the organiser's and the contestant's pages.

The tests run against a stack that is already up with both profiles, `app`
and `agent`, and are skipped unless `UNICON_E2E_URL` names the app, for
example `http://localhost:8080`. `UNICON_E2E_COMPOSE` is the compose command
the accounts are made with, run from this repository; it defaults to the dev
stack's own files.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import FilePayload, Locator, Page, expect

APP = os.environ.get("UNICON_E2E_URL", "").rstrip("/")
COMPOSE = shlex.split(
    os.environ.get(
        "UNICON_E2E_COMPOSE",
        "docker compose -f compose.yaml -f compose.dev.yaml --profile app",
    )
)
DEPLOY = Path(__file__).resolve().parents[2]
VERDICT_TIMEOUT_MS = 300_000
CREATE_TIMEOUT_MS = 60_000

SAMPLE = "a, b = map(int, input().split())\nprint(a + b)\n"
WRONG = "a, b = map(int, input().split())\nprint(a - b)\n"

needs_stack = pytest.mark.skipif(
    not APP, reason="UNICON_E2E_URL names no running stack"
)


def create_account(username: str) -> str:
    """A person made with `unicon create-account`; their first password."""
    run = subprocess.run(
        [
            *COMPOSE,
            "exec",
            "-T",
            "backend",
            "unicon",
            "create-account",
            username,
            "--email",
            f"{username}@unicon.invalid",
        ],
        cwd=DEPLOY,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    found = re.search(r"First password: (\S+)", run.stdout)
    assert found, "create-account printed no first password"
    return found.group(1)


def sign_in(page: Page, username: str, password: str) -> None:
    """Through the app's sign-in link and Forgejo's pages: the sign-in form,
    the change of the first password, and the consent page when it asks.
    Each step waits for the address to leave the page it was on, so what it
    reads next is the page the last one led to.
    """
    page.goto(f"{APP}/login?next=%2F")
    page.get_by_role("main").get_by_role(
        "link", name=re.compile("sign in", re.I)
    ).first.click()
    page.wait_for_url(re.compile(r"/user/login"))
    page.locator('input[name="user_name"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator("form button.primary, form button[type=submit]").first.click()
    page.wait_for_url(lambda url: "/user/login" not in url)
    if "change_password" in page.url:
        fresh = f"Pw-{secrets.token_hex(8)}"
        page.locator('input[name="password"]').fill(fresh)
        page.locator('input[name="retype"]').fill(fresh)
        page.locator("form button.primary, form button[type=submit]").first.click()
        page.wait_for_url(lambda url: "change_password" not in url)
    if "/login/oauth/" in page.url:
        page.locator(
            'button[name="granted"][value="true"], #authorize-app'
        ).first.click()
    page.wait_for_url(f"{APP}/")


def create(page: Page, name: str, button: str, start: str | None = None) -> None:
    """Make `name` with the page's create form, opened first with the button
    `start` when the form is behind one, and open its page: the new org's
    page comes up by itself, and a new contest or task is a link in the list
    once it is made. The new page is known by its title: the router swaps
    pages in a transition, so for a moment the address is the new page's
    while the old one is still shown.
    """
    if start is not None:
        page.get_by_role("button", name=start, exact=True).click()
    page.get_by_role("textbox", name=re.compile("^Name")).fill(name)
    page.get_by_role("button", name=button, exact=True).click()
    title = page.get_by_role("heading", name=name, level=1, exact=True)
    if start is not None:
        page.get_by_role("link", name=name, exact=True).click(timeout=CREATE_TIMEOUT_MS)
    expect(title).to_be_visible(timeout=CREATE_TIMEOUT_MS)


def open_settings(page: Page, form: str) -> Locator:
    """Open the page's settings section and answer its form, by name."""
    page.get_by_role("button", name="Edit the settings", exact=True).click()
    found = page.get_by_role("form", name=form)
    expect(found).to_be_visible()
    return found


def local(moment: datetime) -> str:
    """`moment` as a `datetime-local` field takes it, in the browser's zone,
    which is the machine's: UTC on the CI's runner.
    """
    return moment.astimezone().strftime("%Y-%m-%dT%H:%M")


def submit(page: Page, number: int, source: str) -> None:
    """Send `source` as the task's Python solution from the submit panel. The
    starter task offers one language, so the panel names it rather than
    asking for it.
    """
    form = page.get_by_role("form", name="Submit")
    solution: FilePayload = {
        "name": "main.py",
        "mimeType": "text/x-python",
        "buffer": source.encode(),
    }
    form.get_by_label("Your solution", exact=True).set_input_files(files=[solution])
    expect(form.get_by_text("language: python")).to_be_visible()
    form.get_by_role("button", name="Submit", exact=True).click()
    expect(page.get_by_text(f"Submitted as #{number}.")).to_be_visible()


def verdict_of(page: Page, number: int) -> str:
    """The outcome the contestant's list shows for submission `number`, once
    its grading has finished.
    """
    row = (
        page.get_by_role("table", name="Your submissions")
        .get_by_role("row")
        .filter(has=page.get_by_role("link", name=f"#{number}", exact=True))
    )
    final = re.compile(
        r"ACCEPTED|WRONG ANSWER|TIME LIMIT|MEMORY LIMIT|OUTPUT LIMIT|RUNTIME ERR|"
        r"COMPILE ERR|SYSTEM ERR|FAILED|CANCELLED"
    )
    expect(row).to_contain_text(final, timeout=VERDICT_TIMEOUT_MS)
    found = final.search(row.inner_text())
    assert found
    return found.group(0)


def upload_into_task(page: Page, path: str, content: bytes) -> None:
    """Send `content` through the upload door as the task's file at `path`
    and save it in, from the task's page. The upload stays open after a save,
    with its outcome, for the next file.
    """
    upload = page.get_by_role("region", name="Upload a file")
    if not upload.is_visible():
        page.get_by_role("button", name="Upload a file", exact=True).click()
    data: FilePayload = {
        "name": path.rsplit("/", 1)[-1],
        "mimeType": "application/octet-stream",
        "buffer": content,
    }
    upload.get_by_label("File to upload", exact=True).set_input_files(files=[data])
    upload.get_by_label("Path in the task").fill(path)
    upload.get_by_role("button", name="Upload", exact=True).click()
    upload.get_by_role("button", name="Save into the task").click(
        timeout=CREATE_TIMEOUT_MS
    )
