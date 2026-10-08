"""Rank one contest's gradings on three boards on the running compose stack,
read before and after the task's reveal by a contestant, a visitor and an
organiser.

The organiser gives the starter task a second group of tests, `final`,
shown only after the close, so `main` and `final` each carry half of the
task's 100 points, and builds the contest's three boards in its settings
form: `IOI`, on points, shown to everyone; `ICPC`, on points and then
penalty at 20 minutes an earlier attempt, shown to contestants; and `Final`,
over the groups shown after the close, counting the submission each row
marked, shown to contestants. The contestant reads the task's countdown,
sends a wrong solution and then the right one, and marks the right one.

Before the reveal every board counts only what is shown: IOI and ICPC give
the right solution `main`'s 50 points, ICPC with the wrong attempt before it
in its penalty, and Final shows nothing yet; a visitor, signed out, sees
IOI and not ICPC; the organiser sees Final `now` as the contestant does and
Final `final` with the 50 points of `final` already. The organiser closes
the task, and every board counts `final` too.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from playwright.sync_api import Browser, Locator, Page, ViewportSize, expect

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

SAVED = re.compile(r"Published as publication \d+\.|Saved as a draft")


def until_shown(
    page: Page, find: Callable[[], Locator], text: str | re.Pattern[str]
) -> None:
    """Read `find` again, reloading the page, until it shows `text`: a board
    is read when its page loads, and a grading lands on it when it finishes.
    """
    deadline = time.monotonic() + VERDICT_TIMEOUT_MS / 1000
    while True:
        try:
            expect(find()).to_contain_text(text, timeout=10_000)
            return
        except AssertionError:
            if time.monotonic() > deadline:
                raise
            page.reload()


def board(page: Page, name: str) -> Locator:
    return page.get_by_role("region", name=name, exact=True)


def row_of(page: Page, table: str, who: str) -> Locator:
    return (
        page.get_by_role("table", name=table, exact=True)
        .get_by_role("row")
        .filter(has=page.get_by_role("rowheader", name=re.compile(re.escape(who))))
    )


def cell(page: Page, table: str, who: str, index: int) -> Callable[[], Locator]:
    """The `index`th data cell of `who`'s row on `table`, after the rank."""
    return lambda: row_of(page, table, who).get_by_role("cell").nth(index + 1)


def test_three_boards_count_what_is_shown_before_and_after_the_reveal(
    browser: Browser,
) -> None:
    stamp = secrets.token_hex(3)
    organiser_name, contestant_name = f"e2e-o-{stamp}", f"e2e-c-{stamp}"
    org = f"e2e-org-{stamp}"
    organiser_password = create_account(organiser_name)
    contestant_password = create_account(contestant_name)

    viewport: ViewportSize = {"width": 1280, "height": 900}
    organiser = browser.new_context(viewport=viewport).new_page()
    contestant = browser.new_context(viewport=viewport).new_page()
    visitor = browser.new_context(viewport=viewport).new_page()
    for page in (organiser, contestant, visitor):
        page.set_default_timeout(30_000)

    sign_in(organiser, organiser_name, organiser_password)
    organiser.goto(f"{APP}/orgs/new")
    create(organiser, org, "Create org")
    create(organiser, "spring", "Create contest", "New contest")
    contest_page = organiser.url
    create(organiser, "sum", "Create task", "New task")

    task_page = organiser.url
    upload = organiser.get_by_role("region", name="Upload a file")
    for path, content in (("input", b"5 7\n"), ("answer", b"12\n")):
        upload_into_task(organiser, f"tests/final/1/{path}", content)
        expect(upload.get_by_text(SAVED)).to_be_visible(timeout=CREATE_TIMEOUT_MS)

    organiser.goto(task_page)
    settings = open_settings(organiser, "Task settings")
    settings.get_by_label("final each", exact=True).fill("100")
    settings.get_by_label("final show", exact=True).select_option("after_close")
    settings.get_by_label("Rate: count").fill("10")
    settings.get_by_label("Rate: per seconds").fill("60")
    settings.get_by_role("button", name="Save settings").click()
    expect(
        organiser.get_by_text(re.compile(r"Published as publication \d+\."))
    ).to_be_visible()

    organiser.goto(contest_page)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    settings = open_settings(organiser, "Contest settings")
    field = settings.get_by_label
    field("Name", exact=True).fill(f"Spring {stamp}")
    field("State", exact=True).select_option("published")
    field("Who sees it", exact=True).select_option("everyone")
    field("Start", exact=True).fill(local(now - timedelta(hours=1)))
    field("End", exact=True).fill(local(now + timedelta(hours=3)))
    field("A worth", exact=True).fill("100")
    field("A closes", exact=True).fill(local(now + timedelta(hours=2)))

    field("Board 1 name", exact=True).fill("IOI")
    field("Board 1 shown to", exact=True).select_option("everyone")

    settings.get_by_role("button", name="Add a board").click()
    field("Board 2 name", exact=True).fill("ICPC")
    field("Board 2 shown to", exact=True).select_option("contestants")
    settings.get_by_role("button", name="Add a key to Board 2").click()
    field("Board 2 key 2", exact=True).select_option("penalty")
    field("Board 2 key 2 minutes per earlier attempt", exact=True).fill("20")

    settings.get_by_role("button", name="Add a board").click()
    field("Board 3 name", exact=True).fill("Final")
    field("Board 3 shown to", exact=True).select_option("contestants")
    field("Board 3 counts groups", exact=True).select_option("after_close")
    field("Board 3 counts which submission", exact=True).select_option("marked")

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
    expect(contestant.get_by_role("timer", name="Task countdown")).to_contain_text(
        re.compile(r"Closes in 1h [345]\dm")
    )

    submit(contestant, 1, WRONG)
    assert verdict_of(contestant, 1) == "WRONG ANSWER"
    submit(contestant, 2, SAMPLE)
    assert verdict_of(contestant, 2) == "ACCEPTED"
    mark = contestant.get_by_role("checkbox", name="Mark #2", exact=True)
    mark.click()
    expect(mark).to_be_checked()

    boards = f"{APP}/contests/{org}/spring/boards"
    contestant.goto(boards)
    until_shown(contestant, cell(contestant, "IOI", contestant_name, 0), "50")
    expect(cell(contestant, "ICPC", contestant_name, 0)()).to_have_text("50")
    penalty = cell(contestant, "ICPC", contestant_name, 1)().inner_text()
    assert 80 <= float(penalty) <= 120, penalty
    expect(cell(contestant, "ICPC", contestant_name, 2)()).to_contain_text(
        "1 attempt before"
    )
    expect(board(contestant, "Final")).to_contain_text("Shown from")

    visitor.goto(boards)
    until_shown(visitor, cell(visitor, "IOI", contestant_name, 0), "50")
    expect(board(visitor, "ICPC")).to_have_count(0)
    expect(board(visitor, "Final")).to_have_count(0)

    organiser.goto(f"{APP}/orgs/{org}/contests/spring/boards")
    expect(board(organiser, "Final now")).to_contain_text("Shown from")
    until_shown(organiser, cell(organiser, "Final final", contestant_name, 0), "50")
    expect(cell(organiser, "IOI now", contestant_name, 0)()).to_have_text("50")
    expect(cell(organiser, "IOI final", contestant_name, 0)()).to_have_text("100")

    organiser.goto(contest_page)
    settings = open_settings(organiser, "Contest settings")
    settings.get_by_label("A closes", exact=True).fill(
        local(datetime.now(UTC) - timedelta(minutes=1))
    )
    settings.get_by_role("button", name="Save settings").click()
    expect(organiser.get_by_text(re.compile(r"Saved as version"))).to_be_visible()

    contestant.goto(boards)
    until_shown(contestant, cell(contestant, "IOI", contestant_name, 0), "100")
    expect(cell(contestant, "ICPC", contestant_name, 0)()).to_have_text("100")
    expect(cell(contestant, "ICPC", contestant_name, 1)()).to_have_text(penalty)
    until_shown(contestant, cell(contestant, "Final", contestant_name, 0), "50")

    visitor.goto(boards)
    until_shown(visitor, cell(visitor, "IOI", contestant_name, 0), "100")
