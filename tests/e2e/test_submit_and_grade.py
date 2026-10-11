"""Run a contest from the organiser's pages and grade on the running compose
stack, from the browser.

An organiser and an entrant, each made with the operator's command and
signed in through Forgejo's own pages. The organiser creates an org, a
contest and a task from the organiser pages, checks the task grades with
unicon/classic@v2, sets its submission rate on the task's page so the task
publishes, sets the contest running now from its settings and opens it
from its status card. The entrant registers, the organiser approves them
from the Entrants page, and the entrant submits the task's sample solution
and then a wrong one from the submit panel. The first comes back accepted
and the second wrong answer, graded by the real CI, a real grading machine
and the primitives at the forge. The organiser then replaces the input of
the task's test from its Tests card; since the contest is running and the
test grades, the save asks first, the organiser publishes the change, and
every submission is graded again, which the contest's gradings feed shows
as a second attempt of the sample solution, accepted. Then the entrant
signs out, and signing in again asks Forgejo for their password: signing
out of the app signed the browser out of Forgejo too.

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
    approve,
    contest_settings,
    create,
    create_account,
    create_org,
    local,
    needs_stack,
    open_contest,
    register,
    replace_test,
    save_task,
    sign_in,
    submission_rate,
    submit,
    verdict_of,
)

pytestmark = [needs_stack]


def test_a_right_and_a_wrong_solution_get_their_verdicts(browser: Browser) -> None:
    stamp = secrets.token_hex(3)
    organiser_name, entrant_name = f"e2e-o-{stamp}", f"e2e-c-{stamp}"
    org = f"e2e-org-{stamp}"
    organiser_password = create_account(organiser_name)
    entrant_password = create_account(entrant_name)

    viewport: ViewportSize = {"width": 1280, "height": 900}
    organiser = browser.new_context(viewport=viewport).new_page()
    entrant = browser.new_context(viewport=viewport).new_page()
    for page in (organiser, entrant):
        page.set_default_timeout(30_000)

    sign_in(organiser, organiser_name, organiser_password)
    create_org(organiser, org)
    create(organiser, "spring", "Create contest", "New contest")
    create(organiser, "sum", "Create task", "New task")

    task_page = organiser.url
    workflow = organiser.get_by_role("region", name="Workflow")
    expect(workflow.get_by_role("link", name="Open unicon/classic@v2")).to_be_visible()
    rules = organiser.get_by_role("region", name="Submission rules")
    expect(rules.get_by_role("textbox", name="Submissions in a window")).to_have_value(
        ""
    )
    submission_rate(organiser, 10, 60)
    save_task(organiser)

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    contest_settings(
        organiser,
        org,
        "spring",
        Name=f"Spring {stamp}",
        Start=local(now - timedelta(hours=1)),
        End=local(now + timedelta(hours=3)),
    )
    open_contest(organiser, org, "spring")

    sign_in(entrant, entrant_name, entrant_password)
    register(entrant, org, "spring")
    approve(organiser, org, "spring", entrant_name)

    expect(entrant.get_by_text("You are in")).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    entrant.goto(f"{APP}/contests/{org}/spring/tasks/sum")
    expect(entrant.get_by_role("form", name="Submit")).to_be_visible()

    submit(entrant, 1, SAMPLE)
    assert verdict_of(entrant, 1) == "ACCEPTED"
    submit(entrant, 2, WRONG)
    assert verdict_of(entrant, 2) == "WRONG ANSWER"

    organiser.goto(f"{task_page}?card=tests")
    replace_test(organiser, "main", "1", {"input": b"2 1\n"})
    save_task(organiser)

    organiser.goto(f"{APP}/orgs/{org}/contests/spring/gradings")
    first = (
        organiser.get_by_role("table", name="Gradings")
        .get_by_role("row")
        .filter(has_text="Submission 1")
        .filter(has_text="Show earlier attempts (1)")
    )
    expect(first).to_contain_text("ACCEPTED", timeout=VERDICT_TIMEOUT_MS)

    entrant.get_by_role("button", name="Account menu").click()
    with entrant.expect_event("load"):
        entrant.get_by_role("menuitem", name="Sign out").click()
    assert entrant.url == f"{APP}/"
    entrant.goto(f"{APP}/login?next=%2F")
    entrant.get_by_role("main").get_by_role(
        "link", name=re.compile("sign in", re.I)
    ).first.click()
    entrant.wait_for_url(re.compile(r"/user/login"))
    expect(entrant.locator('input[name="password"]')).to_be_visible()
