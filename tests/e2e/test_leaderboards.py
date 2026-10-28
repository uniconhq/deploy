"""Rank one contest on a leaderboard that reads a value hidden until the
close, on the running compose stack.

The organiser builds the starter task's two runs on the Runs card, one
shown now and one after the close, and the values on the Scoring card:
`main_points` and `final_points`, 50 for a run that passes, and `total`,
their sum, shown only after the close. The contest's leaderboard reads
`total`. Before the close an entrant sees a dash there, not the points so
far, and staff see the number. After the close the entrant sees it too.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from playwright.sync_api import Browser, Locator, Page, expect

from tests.e2e.stack import (
    APP,
    CREATE_TIMEOUT_MS,
    SAMPLE,
    STATEMENT,
    VERDICT_TIMEOUT_MS,
    approve,
    contest_settings,
    create,
    create_account,
    create_org,
    local,
    needs_stack,
    open_contest,
    open_page,
    register,
    reload_page,
    save_task,
    save_until,
    sign_in,
    status_of,
    submission_rate,
    submit,
    task_timeline,
    upload_run_file,
    write_statement,
)

pytestmark = [needs_stack]


def until_shown(
    page: Page, find: Callable[[], Locator], text: str | re.Pattern[str]
) -> None:
    """Read `find` again, reloading the page, until it shows `text`."""
    deadline = time.monotonic() + VERDICT_TIMEOUT_MS / 1000
    while True:
        try:
            expect(find()).to_contain_text(text, timeout=10_000)
            return
        except AssertionError:
            if time.monotonic() > deadline:
                raise
            reload_page(page)


def cell(page: Page, table: str, who: str, index: int) -> Callable[[], Locator]:
    """The `index`th data cell of `who`'s row on `table`, after the place and
    the entrant."""
    row = (
        page.get_by_role("table", name=table, exact=True)
        .get_by_role("row")
        .filter(has=page.get_by_role("cell", name=re.compile(re.escape(who), re.I)))
    )
    return lambda: row.get_by_role("cell").nth(index + 2)


def formula(page: Page, box: Locator, text: str) -> None:
    """Replace the formula with `text`. A new column already says `1`.
    ControlOrMeta selects that on the runner and on a Mac, where Meta
    alone does not, so the typed line replaces it instead of sticking
    on the end. Escape then closes the completion menu.
    """
    box.click()
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.insert_text(text)
    page.keyboard.press("Escape")


def add_value(page: Page, scoring: Locator, text: str, name: str, when: str) -> None:
    scoring.get_by_role("button", name="Add a value").click()
    line = scoring.get_by_role("textbox", name=re.compile(r"^value_\d+$")).last
    formula(page, line, text)
    named = scoring.get_by_role("textbox", name="Name").last
    named.fill(name)
    named.press("Tab")
    shown(scoring, name, when)


def shown(scoring: Locator, name: str, when: str) -> None:
    """When the value `name` is shown to entrants, set on its own card."""
    scoring.get_by_role("group", name=f"Value {name}").get_by_role(
        "combobox", name="Shown to entrants"
    ).select_option(label=when)


def add_group(
    page: Page, scoring: Locator, name: str, rows: list[str], when: str
) -> None:
    scoring.get_by_role("button", name="Add a group").click()
    box = scoring.locator("input[aria-label^='Name of group_']").last
    box.fill(name)
    page.keyboard.press("Tab")
    for row in rows:
        scoring.get_by_role("checkbox", name=f"{row} in {name}").check()
    scoring.get_by_label(f"When {name} is shown").select_option(label=when)


def test_a_hidden_total_is_a_dash_until_the_close_and_staff_see_it(
    browser: Browser,
) -> None:
    stamp = secrets.token_hex(3)
    organiser_name, entrant_name = f"e2e-o-{stamp}", f"e2e-c-{stamp}"
    org = f"e2e-org-{stamp}"
    organiser_password = create_account(organiser_name)
    entrant_password = create_account(entrant_name)

    organiser = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    entrant = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    for page in (organiser, entrant):
        page.set_default_timeout(30_000)

    sign_in(organiser, organiser_name, organiser_password)
    create_org(organiser, org)
    create(organiser, "spring", "Create contest", "New contest")
    create(organiser, "sum", "Create task", "New task")
    task_page = organiser.url

    runs = organiser.get_by_role("region", name="Runs")
    runs.get_by_role("textbox", name="Name of row 1").fill("Main")
    runs.get_by_role("button", name="Add a row").click()
    runs.get_by_role("textbox", name="Name of row 2").fill("Final")
    upload_run_file(organiser, 2, "input", b"5 7\n")
    upload_run_file(organiser, 2, "answer", b"12\n")

    scoring = organiser.get_by_role("region", name="Scoring")
    add_group(organiser, scoring, "main_tests", ["Main"], "Now")
    add_group(organiser, scoring, "final_tests", ["Final"], "After the close")
    add_value(organiser, scoring, "50 * sum(main_tests.passed)", "main_points", "Now")
    add_value(
        organiser,
        scoring,
        "50 * sum(final_tests.passed)",
        "final_points",
        "After the close",
    )
    add_value(
        organiser, scoring, "main_points + final_points", "total", "After the close"
    )
    shown(scoring, "score", "Not shown")
    submission_rate(organiser, 10, 60)
    save_task(organiser)
    write_statement(organiser, task_page, STATEMENT)

    open_page(organiser, f"{APP}/orgs/{org}/contests/spring/leaderboards")
    organiser.get_by_role("button", name="Add a leaderboard").click()
    organiser.get_by_role(
        "region", name="Leaderboard leaderboard", exact=True
    ).get_by_role("textbox", name="Name", exact=True).first.fill("IOI")
    board = organiser.get_by_role("region", name="Leaderboard ioi", exact=True)
    board.get_by_role("radio", name="Anyone").check()
    formula(
        organiser,
        board.get_by_role("group", name="points").get_by_role("textbox", name="points"),
        "latest(sum).total",
    )
    save_until(
        organiser,
        organiser.get_by_role("button", name="Save", exact=True),
        organiser.get_by_text("Saved.", exact=True),
    )

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    contest_settings(
        organiser,
        org,
        "spring",
        Name=f"Spring {stamp}",
        Start=local(now - timedelta(hours=1)),
        End=local(now + timedelta(days=2)),
    )
    task_timeline(organiser, org, "spring", "A · sum", closes=now + timedelta(hours=2))
    open_page(organiser, f"{APP}/orgs/{org}/contests/spring")
    organiser.get_by_role("button", name="Change who sees it").click()
    seen = organiser.get_by_role("dialog", name="Who sees it")
    seen.get_by_role("radio", name="Anyone, signed in or not").check()
    seen.get_by_role("button", name="Save").click()
    expect(seen).to_be_hidden(timeout=CREATE_TIMEOUT_MS)
    open_contest(organiser, org, "spring")

    sign_in(entrant, entrant_name, entrant_password)
    register(entrant, org, "spring")
    approve(organiser, org, "spring", entrant_name)
    expect(entrant.get_by_text("You are in")).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    open_page(entrant, f"{APP}/contests/{org}/spring/tasks/sum")
    submit(entrant, 1, SAMPLE)
    assert status_of(entrant, 1) == "GRADED"

    public = f"{APP}/contests/{org}/spring/leaderboards"
    open_page(entrant, public)
    until_shown(entrant, cell(entrant, "IOI", entrant_name, 0), "\u2013")
    open_page(organiser, public)
    until_shown(organiser, cell(organiser, "IOI", entrant_name, 0), "100")

    last = datetime.now(UTC)
    reveal = last.replace(second=0, microsecond=0) + timedelta(minutes=1)
    time.sleep(max(0.0, (reveal - datetime.now(UTC)).total_seconds()) + 1)
    task_timeline(organiser, org, "spring", "A · sum", closes=reveal)
    open_page(entrant, public)
    until_shown(entrant, cell(entrant, "IOI", entrant_name, 0), "100")
