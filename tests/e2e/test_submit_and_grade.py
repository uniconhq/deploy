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

import re
import secrets
from datetime import UTC, datetime, timedelta

from playwright.sync_api import Browser, ViewportSize, expect

from tests.e2e.stack import (
    APP,
    CREATE_TIMEOUT_MS,
    SAMPLE,
    VERDICT_TIMEOUT_MS,
    WRONG,
    create,
    create_account,
    local,
    needs_stack,
    open_settings,
    sign_in,
    submit,
    upload_into_task,
    verdict_of,
)

pytestmark = [needs_stack]


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
    settings.get_by_label("Name", exact=True).fill(f"Spring {stamp}")
    settings.get_by_label("State", exact=True).select_option("published")
    settings.get_by_label("Start", exact=True).fill(local(now - timedelta(hours=1)))
    settings.get_by_label("End", exact=True).fill(local(now + timedelta(hours=3)))
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
    upload_into_task(organiser, "tests/main/1/input", b"2 1\n")
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
