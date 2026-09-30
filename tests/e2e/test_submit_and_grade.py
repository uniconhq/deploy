"""Submit and grade on the running compose stack, from the browser.

An organiser and a contestant, each made with the operator's command and
signed in through Forgejo's own pages. The organiser creates an org, a
contest and a task from the organiser pages, saves `task.yaml` so the task
publishes, and saves `contest.yaml` so the contest is published and running.
The contestant registers, the organiser approves them from the contestants
table, and the contestant submits the task's sample solution and then a
wrong one from the submit panel. The first comes back `accepted` and the
second `wrong_answer`, graded by the real CI, a real grading machine and the
primitives at the forge.

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
PROVISIONING_TIMEOUT_MS = 120_000

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


def create(page: Page, name: str, button: str, opened: str) -> None:
    """Make `name` with the page's create form, follow it until it is made,
    and open its page through the link that then appears. The new page is
    known by its title: the router swaps pages in a transition, so for a
    moment the address is the new page's while the old one is still shown.
    """
    page.get_by_role("textbox", name=re.compile("^Name")).fill(name)
    page.get_by_role("button", name=button, exact=True).click()
    page.get_by_role("link", name=opened, exact=True).click(
        timeout=PROVISIONING_TIMEOUT_MS
    )
    expect(page.get_by_role("heading", name=name, level=1, exact=True)).to_be_visible()


def edit_and_save(page: Page, file: str, change: str | None = None) -> None:
    """Open `file` in the organiser's file browser, replace its text with
    `change` (or leave it), and press Save.
    """
    page.get_by_role("link", name=file, exact=True).click()
    editor = page.get_by_role("textbox", name=file)
    expect(editor).to_be_visible()
    if change is not None:
        editor.fill(change)
    page.get_by_role("button", name="Save", exact=True).click()


def running_contest(title: str) -> str:
    """A `contest.yaml` for a contest that is published and running now, open
    to register with the organisers' approval.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    start = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f'name: "{title}"\n'
        'description: ""\n'
        f"start: {start}\n"
        f"end: {end}\n"
        "state: published\n"
        "submissions_closed: false\n"
        "visibility: signed-in\n"
        "\n"
        "registration:\n"
        "  mode: open\n"
        "  approval: manual\n"
        "\n"
        "teams:\n"
        "  enabled: false\n"
        "  max_size: 3\n"
        "\n"
        "leaderboards: []\n"
        "\n"
        "tasks: []\n"
    )


def submit(page: Page, number: int, source: str) -> None:
    """Send `source` as the task's Python solution from the submit panel. The
    starter task takes Python alone, so the panel names the language rather
    than asking for it.
    """
    form = page.get_by_role("form", name="Submit")
    solution: FilePayload = {
        "name": "main.py",
        "mimeType": "text/x-python",
        "buffer": source.encode(),
    }
    form.get_by_label("Your solution", exact=True).set_input_files(files=[solution])
    expect(form.get_by_text("In python.")).to_be_visible()
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
    create(organiser, org, "Create org", f"Open the org {org}")
    create(organiser, "spring", "Create contest", "Open the contest spring")
    contest_page = organiser.url
    create(organiser, "sum", "Create task", "Open the task sum")

    organiser.get_by_role("link", name="task.yaml", exact=True).click()
    task_yaml = organiser.get_by_role("textbox", name="task.yaml").input_value()
    loosened = re.sub(r"rate: .*", "rate: 10 per 60s", task_yaml)
    assert loosened != task_yaml, "the starter task.yaml has no rate line"
    edit_and_save(organiser, "task.yaml", loosened)
    expect(
        organiser.get_by_text(re.compile(r"Published as publication \d+\."))
    ).to_be_visible()

    organiser.goto(contest_page)
    edit_and_save(organiser, "contest.yaml", running_contest(f"Spring {stamp}"))
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
        timeout=PROVISIONING_TIMEOUT_MS
    )
    contestant.goto(f"{APP}/contests/{org}/spring/tasks/sum")
    expect(contestant.get_by_role("form", name="Submit")).to_be_visible(
        timeout=PROVISIONING_TIMEOUT_MS
    )

    submit(contestant, 1, SAMPLE)
    assert verdict_of(contestant, 1) == "ACCEPTED"
    submit(contestant, 2, WRONG)
    assert verdict_of(contestant, 2) == "WRONG ANSWER"
