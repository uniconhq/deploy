"""Run a contest from the organiser's forms and grade on the running
compose stack, from the browser.

An organiser and a contestant, each made with the operator's command and
signed in through Forgejo's own pages. The organiser creates an org, a
contest and a task from the organiser pages, sets the task's submission
rate in the task's settings form so the task publishes, and publishes the
contest, running now, from the contest's settings form. The contestant
registers, the organiser approves them from the contestants table, and the
contestant submits the task's sample solution and then a wrong one from the
submit panel. The first comes back `accepted` and the second
`wrong_answer`, graded by the real CI, a real grading machine and the
primitives at the forge. The organiser then uploads a new input for the
task's test through the upload door and saves it in; since the contest is
running and the data grades, the save asks first, the organiser publishes
the change, and every submission is graded again, which the contest's
gradings feed shows as a second attempt of the sample solution, accepted.
Then the contestant signs out, and signing in again asks Forgejo for their
password: signing out of the app signed the browser out of Forgejo too.

It runs against a stack that is already up with both profiles, `app` and
`agent`, and is skipped unless `UNICON_E2E_URL` names the app, for example
`http://localhost:8080`. `UNICON_E2E_COMPOSE` is the compose command the
accounts are made with, run from this repository; it defaults to the dev
stack's own files. Every name it makes carries a random suffix, so it runs
again on the same stack without clearing anything.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import (
    Browser,
    FilePayload,
    Locator,
    Page,
    ViewportSize,
    expect,
    sync_playwright,
)

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

pytestmark = [
    pytest.mark.skipif(not APP, reason="UNICON_E2E_URL names no running stack"),
]


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


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        try:
            yield launched
        finally:
            launched.close()


def test_a_right_and_a_wrong_solution_get_their_verdicts(browser: Browser) -> None:
    stamp = secrets.token_hex(3)
    organiser_name, contestant_name = f"e2e-o-{stamp}", f"e2e-c-{stamp}"
    org = f"e2e-org-{stamp}"
    organiser_password = create_account(organiser_name)
    contestant_password = create_account(contestant_name)

    viewport: ViewportSize = {"width": 1280, "height": 900}
    organiser = browser.new_context(viewport=viewport).new_page()
    contestant = browser.new_context(viewport=viewport).new_page()
    for page in (organiser, contestant):
        page.set_default_timeout(30_000)

    sign_in(organiser, organiser_name, organiser_password)
    organiser.goto(f"{APP}/orgs/new")
    create(organiser, org, "Create org")
    create(organiser, "spring", "Create contest", "New contest")
    contest_page = organiser.url
    create(organiser, "sum", "Create task", "New task")

    task_page = organiser.url
    settings = open_settings(organiser, "Task settings")
    expect(settings.get_by_label("Workflow")).to_have_value("unicon/classic@v2")
    expect(settings.get_by_label("Rate: count")).to_have_value("")
    settings.get_by_label("Rate: count").fill("10")
    settings.get_by_label("Rate: per seconds").fill("60")
    settings.get_by_role("button", name="Save settings").click()
    expect(
        organiser.get_by_text(re.compile(r"Published as publication \d+\."))
    ).to_be_visible()

    organiser.goto(contest_page)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    settings = open_settings(organiser, "Contest settings")
    settings.get_by_label("Name").fill(f"Spring {stamp}")
    settings.get_by_label("State").select_option("published")
    settings.get_by_label("Start").fill(local(now - timedelta(hours=1)))
    settings.get_by_label("End").fill(local(now + timedelta(hours=3)))
    settings.get_by_role("button", name="Save settings").click()
    expect(organiser.get_by_text(re.compile(r"Saved as version"))).to_be_visible()

    sign_in(contestant, contestant_name, contestant_password)
    contestant.goto(f"{APP}/contests/{org}/spring")
    contestant.get_by_role("button", name="Register").click()
    expect(contestant.get_by_text("Your registration is waiting")).to_be_visible()

    organiser.goto(f"{APP}/orgs/{org}/contests/spring/contestants")
    expect(organiser.get_by_role("table", name="Registrations")).to_be_visible()
    organiser.get_by_role("button", name=re.compile("^Approve")).click()
    expect(organiser.get_by_text("Approved")).to_be_visible()

    expect(contestant.get_by_text("You are in")).to_be_visible(
        timeout=CREATE_TIMEOUT_MS
    )
    contestant.goto(f"{APP}/contests/{org}/spring/tasks/sum")
    expect(contestant.get_by_role("form", name="Submit")).to_be_visible()

    submit(contestant, 1, SAMPLE)
    assert verdict_of(contestant, 1) == "ACCEPTED"
    submit(contestant, 2, WRONG)
    assert verdict_of(contestant, 2) == "WRONG ANSWER"

    organiser.goto(task_page)
    organiser.get_by_role("button", name="Upload a file", exact=True).click()
    upload = organiser.get_by_role("region", name="Upload a file")
    data: FilePayload = {
        "name": "input",
        "mimeType": "application/octet-stream",
        "buffer": b"2 1\n",
    }
    upload.get_by_label("File to upload", exact=True).set_input_files(files=[data])
    upload.get_by_label("Path in the task").fill("tests/main/1/input")
    upload.get_by_role("button", name="Upload", exact=True).click()
    upload.get_by_role("button", name="Save into the task").click(
        timeout=CREATE_TIMEOUT_MS
    )
    organiser.get_by_role("button", name="Publish the change").click()
    expect(
        organiser.get_by_text(re.compile(r"Published as publication \d+\."))
    ).to_be_visible()

    organiser.goto(f"{APP}/orgs/{org}/contests/spring/gradings")
    first = (
        organiser.get_by_role("table", name="Gradings")
        .get_by_role("row")
        .filter(has_text="Submission 1")
        .filter(has_text="Show earlier attempts (1)")
    )
    expect(first).to_contain_text("accepted", timeout=VERDICT_TIMEOUT_MS)

    contestant.get_by_role("button", name="Account menu").click()
    with contestant.expect_event("load"):
        contestant.get_by_role("menuitem", name="Sign out").click()
    assert contestant.url == f"{APP}/"
    contestant.goto(f"{APP}/login?next=%2F")
    contestant.get_by_role("main").get_by_role(
        "link", name=re.compile("sign in", re.I)
    ).first.click()
    contestant.wait_for_url(re.compile(r"/user/login"))
    expect(contestant.locator('input[name="password"]')).to_be_visible()
