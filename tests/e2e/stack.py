"""What every end-to-end test on the running stack does the same way:
making people with the operator's command, signing them in through
Forgejo's pages, and driving the organiser's and the entrant's pages.

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
import time
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

# A statement of the task's own: a contest does not open while a task's
# published statement is still the one a new task starts with.
STATEMENT = "# A + B\n\nRead two integers and print their sum.\n"

PUBLISHED = re.compile(r"Saved and published as publication \d+\.")

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


def create(
    page: Page, name: str, button: str, start: str, kind: str | None = None
) -> None:
    """Make `name` with the dialog the button `start` opens on the page, and
    open its page. A new org's page comes up by itself; a new contest or task
    is a row of the list it was made from, a task's row named with its
    letter. `kind`, for a task, is the kind it starts from. The new page is
    known by its title: the router swaps pages in a transition, so for a
    moment the address is the new page's while the old one is still shown.
    """
    page.get_by_role("button", name=start, exact=True).click()
    dialog = page.get_by_role("dialog", name=start)
    dialog.get_by_role("textbox", name="Name", exact=True).fill(name)
    if kind is not None:
        dialog.get_by_role("radio", name=kind).check()
    dialog.get_by_role("button", name=button, exact=True).click()
    expect(dialog).to_be_hidden(timeout=CREATE_TIMEOUT_MS)
    if start != "New org":
        listed = page.get_by_role("list", name=re.compile("^(Contests|Tasks)$"))
        listed.get_by_role(
            "link", name=re.compile(rf"^(\w · )?{re.escape(name)}$")
        ).click(timeout=CREATE_TIMEOUT_MS)
    expect(page.get_by_role("heading", name=name, level=1, exact=True)).to_be_visible(
        timeout=CREATE_TIMEOUT_MS
    )


def create_org(page: Page, name: str) -> None:
    page.goto(f"{APP}/orgs")
    create(page, name, "Create org", "New org")


def save_task(page: Page) -> Locator:
    """Save the task's cards from the save bar, publishing the change when
    the contest is running and the save asks; answers the bar.
    """
    bar = page.get_by_role("region", name="Save the task")
    bar.get_by_role("button", name="Save", exact=True).click()
    publish = bar.get_by_role("button", name="Publish the change")
    outcome = bar.get_by_text(PUBLISHED).or_(publish)
    expect(outcome.first).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    if publish.is_visible():
        publish.click()
        expect(bar.get_by_text(PUBLISHED)).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    return bar


def write_statement(page: Page, task_page: str, text: str) -> None:
    """Write the task's statement on its Statement card and save it, which
    publishes it, publishing the change when the contest is running and the
    save asks. A contest opens only once each task's published statement is
    written, not the starter's placeholder.
    """
    page.goto(f"{task_page}?card=statement")
    page.get_by_role("textbox", name="Statement source").fill(text)
    page.get_by_role("button", name="Save statement").click()
    published = page.get_by_role("status").filter(has_text=PUBLISHED)
    publish = page.get_by_role("alert").get_by_role("button", name="Publish the change")
    expect(published.or_(publish).first).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    if publish.is_visible():
        publish.click()
        expect(published).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    _wait_checked(page, task_page)


def _wait_checked(page: Page, task_page: str) -> None:
    """Wait until the task's latest publication has passed its checks. A save
    that publishes stops the checks of the publication before it, so until
    the new one passes the contest refuses to open with the task as not
    published. The task page does not read its state again by itself, so it
    is loaded again until its chip says Published.
    """
    deadline = time.monotonic() + CREATE_TIMEOUT_MS / 1000
    while True:
        page.goto(task_page)
        checked = page.get_by_text("Published", exact=True)
        checking = page.get_by_text("Checking", exact=True)
        expect(checked.or_(checking).first).to_be_visible(timeout=CREATE_TIMEOUT_MS)
        if checked.is_visible():
            return
        assert time.monotonic() < deadline, "the publication is still being checked"
        page.wait_for_timeout(1_000)


def submission_rate(page: Page, count: int, per: int) -> None:
    """The task's submission rate, in its Submission rules, before a save."""
    rules = page.get_by_role("region", name="Submission rules")
    rules.get_by_role("textbox", name="Submissions in a window").fill(str(count))
    rules.get_by_role("textbox", name="Length of the window (seconds)").fill(str(per))


def upload_run_file(page: Page, row: int, field: str, content: bytes) -> None:
    """Give one cell of the Runs card `content`. `row` is the row's place on
    the page, starting at 1, and the editor stores the file at
    `runs/0/<row - 1>/<field>`."""
    held = Path(f"/tmp/unicon-e2e-{row}-{field}")
    held.write_bytes(content)
    page.get_by_role("region", name="Runs").get_by_label(
        f"File for row {row} {field}"
    ).set_input_files(str(held))


def contest_settings(page: Page, org: str, contest: str, **fields: str) -> None:
    """Fill the contest's Settings form, each field by its label, and save."""
    page.goto(f"{APP}/orgs/{org}/contests/{contest}/settings")
    form = page.get_by_role("form", name="Contest settings")
    expect(form.get_by_role("textbox", name="Name", exact=True)).not_to_have_value("")
    for label, value in fields.items():
        form.get_by_role("textbox", name=label, exact=True).fill(value)
    form.get_by_role("button", name="Save settings").click()
    expect(page.get_by_text("Saved.", exact=True)).to_be_visible(
        timeout=CREATE_TIMEOUT_MS
    )


def open_contest(page: Page, org: str, contest: str) -> None:
    """Open the contest from its status card, once its checklist holds."""
    page.goto(f"{APP}/orgs/{org}/contests/{contest}")
    status = page.get_by_role("region", name="Status")
    status.get_by_role("button", name="Open the contest").click()
    dialog = page.get_by_role("dialog", name="Open the contest?")
    dialog.get_by_role("button", name="Open the contest").click()
    expect(dialog).to_be_hidden(timeout=CREATE_TIMEOUT_MS)
    expect(status.get_by_text("Live", exact=True)).to_be_visible()


def task_timeline(
    page: Page, org: str, contest: str, row: str, **times: datetime
) -> None:
    """A task's timeline from the line on its row on the contest's page,
    which opens the task's place in the contest: each of `opens`, `due` and
    `closes` given set at that time.
    """
    page.goto(f"{APP}/orgs/{org}/contests/{contest}")
    page.get_by_role("button", name=f"Change {row} in this contest").click()
    dialog = page.get_by_role("dialog", name=f"{row} in this contest")
    for which, moment in times.items():
        word = which.capitalize()
        dialog.get_by_role("radiogroup", name=word).get_by_role(
            "radio", name="At a time"
        ).check()
        dialog.get_by_label(f"{word} at", exact=True).fill(local(moment))
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog.get_by_role("status").filter(has_text="Saved")).to_be_visible(
        timeout=CREATE_TIMEOUT_MS
    )
    dialog.get_by_role("button", name="Close", exact=True).click()
    expect(dialog).to_be_hidden()


def register(page: Page, org: str, contest: str) -> None:
    """Register alone for the contest from its page, as anyone may by the
    contest's starting sign-up, which an organiser approves.
    """
    page.goto(f"{APP}/contests/{org}/{contest}")
    page.get_by_role("form", name="Register").get_by_role(
        "button", name="Register", exact=True
    ).click()
    expect(page.get_by_text("Your registration is waiting")).to_be_visible()


def approve(organiser: Page, org: str, contest: str, who: str) -> None:
    """Approve `who`'s registration from the contest's Entrants page."""
    organiser.goto(f"{APP}/orgs/{org}/contests/{contest}/entrants")
    row = (
        organiser.get_by_role("table", name="Entrants")
        .get_by_role("row")
        .filter(
            has=organiser.get_by_role(
                "rowheader", name=re.compile(re.escape(who), re.I)
            )
        )
    )
    row.get_by_role("button", name=re.compile("^Approve")).click()
    expect(row).to_contain_text("Approved")


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
    expect(form.get_by_text("Language: python")).to_be_visible()
    form.get_by_role("button", name="Submit", exact=True).click()
    expect(page.get_by_text(f"Submitted as #{number}.")).to_be_visible()


FINAL = re.compile(r"GRADED|GRADING DELAYED|CANCELLED|OVER THE LIMIT")
"""The statuses the entrant's list ends on: the run model gives a
submission no verdict of its own, so the list says where its grading stands."""


def status_of(page: Page, number: int) -> str:
    """The status the entrant's list shows for submission `number`, once
    its grading has ended.
    """
    row = (
        page.get_by_role("table", name="Your submissions")
        .get_by_role("row")
        .filter(has=page.get_by_role("link", name=f"#{number}", exact=True))
    )
    expect(row).to_contain_text(FINAL, timeout=VERDICT_TIMEOUT_MS)
    found = FINAL.search(row.inner_text())
    assert found
    return found.group(0)
