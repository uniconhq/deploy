"""Rank one contest's gradings on three boards on the running compose stack,
read before and after the task's reveal by two contestants, a visitor and an
organiser.

The organiser gives the starter task a second group of tests, `final`,
shown only after the close, so `main` and `final` each carry half of the
task's 100 points, and builds the contest's three boards in its settings
form: `IOI`, on points, shown to everyone; `ICPC`, on points and then
penalty at 20 minutes an earlier attempt, shown to contestants; and `Final`,
over the groups shown after the close, counting the submission each row
marked, shown to contestants. Two people register and are approved; the
second, the rival, has a browser whose clock is three hours fast, and reads
the task's countdown by the server's clock all the same.

The contestant sends a wrong solution and then the right one and marks the
wrong one: the organiser's Final `final` counts it, 0, not the better one.
Moving the mark to the right one gives Final `final` its 50. Before the
rival submits, they are on IOI as a bare row below. Before the reveal every
board counts only what is shown: IOI and ICPC give the right solution
`main`'s 50 points, ICPC with a penalty of the minutes from the start to
it and 20 for the wrong attempt before it, exactly; Final shows nothing
yet; a visitor, signed out, sees IOI and not ICPC, on the contest's own
page and on its boards; the organiser sees Final `now` as the contestant
does. The rival's right solution ties them on IOI and, with no wrong
attempt, puts them first on ICPC. The organiser closes the task, and every
board counts `final` too.
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


def rank(page: Page, table: str, who: str) -> Callable[[], Locator]:
    """The rank on `who`'s row of `table`."""
    return lambda: row_of(page, table, who).get_by_role("cell").first


def register(page: Page, org: str) -> None:
    page.goto(f"{APP}/contests/{org}/spring")
    page.get_by_role("button", name="Register").click()
    expect(page.get_by_text("Your registration is waiting")).to_be_visible()


def approve(organiser: Page, who: str) -> None:
    """Approve `who`'s registration from the contest's contestants page."""
    row = (
        organiser.get_by_role("table", name="Registrations")
        .get_by_role("row")
        .filter(has=organiser.get_by_role("rowheader", name=re.compile(re.escape(who))))
    )
    row.get_by_role("button", name=re.compile("^Approve")).click()
    expect(row).to_contain_text("Approved")


def toggle_mark(page: Page, number: int, checked: bool) -> None:
    mark = page.get_by_role("checkbox", name=f"Mark #{number}", exact=True)
    mark.click()
    if checked:
        expect(mark).to_be_checked()
    else:
        expect(mark).not_to_be_checked()


def submitted_at(page: Page, org: str, number: int) -> datetime:
    """When the reader's submission `number` to the task was made, as the
    platform keeps it, read with the page's own session.
    """
    answer = page.request.get(
        f"{APP}/api/v1/orgs/{org}/contests/spring/tasks/sum/submissions/{number}"
    )
    assert answer.ok, answer.text()
    return datetime.fromisoformat(answer.json()["submitted_at"])


def test_three_boards_count_what_is_shown_before_and_after_the_reveal(
    browser: Browser,
) -> None:
    stamp = secrets.token_hex(3)
    organiser_name, contestant_name = f"e2e-o-{stamp}", f"e2e-c-{stamp}"
    rival_name = f"e2e-r-{stamp}"
    org = f"e2e-org-{stamp}"
    organiser_password = create_account(organiser_name)
    contestant_password = create_account(contestant_name)
    rival_password = create_account(rival_name)

    viewport: ViewportSize = {"width": 1280, "height": 900}
    organiser = browser.new_context(viewport=viewport).new_page()
    contestant = browser.new_context(viewport=viewport).new_page()
    visitor = browser.new_context(viewport=viewport).new_page()
    fast = browser.new_context(viewport=viewport)
    fast.clock.install(time=datetime.now(UTC) + timedelta(hours=3))
    rival = fast.new_page()
    for page in (organiser, contestant, visitor, rival):
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
    register(contestant, org)
    sign_in(rival, rival_name, rival_password)
    register(rival, org)

    organiser.goto(f"{APP}/orgs/{org}/contests/spring/contestants")
    expect(organiser.get_by_role("table", name="Registrations")).to_be_visible()
    approve(organiser, contestant_name)
    approve(organiser, rival_name)

    for page in (contestant, rival):
        expect(page.get_by_text("You are in")).to_be_visible(timeout=CREATE_TIMEOUT_MS)
        page.goto(f"{APP}/contests/{org}/spring/tasks/sum")
        expect(page.get_by_role("timer", name="Task countdown")).to_contain_text(
            re.compile(r"Closes in 1h [345]\dm")
        )

    submit(contestant, 1, WRONG)
    assert verdict_of(contestant, 1) == "WRONG ANSWER"
    submit(contestant, 2, SAMPLE)
    assert verdict_of(contestant, 2) == "ACCEPTED"
    toggle_mark(contestant, 1, checked=True)

    organiser.goto(f"{APP}/orgs/{org}/contests/spring/boards")
    until_shown(organiser, cell(organiser, "IOI final", contestant_name, 0), "100")
    expect(cell(organiser, "Final final", contestant_name, 0)()).to_have_text("0")

    toggle_mark(contestant, 1, checked=False)
    toggle_mark(contestant, 2, checked=True)

    organiser.reload()
    expect(board(organiser, "Final now")).to_contain_text("Shown from")
    until_shown(organiser, cell(organiser, "Final final", contestant_name, 0), "50")
    expect(cell(organiser, "IOI now", contestant_name, 0)()).to_have_text("50")

    # Whole minutes from the contest's start to the right solution, and 20
    # for the wrong attempt before it.
    start = now - timedelta(hours=1)
    minutes = int((submitted_at(contestant, org, 2) - start).total_seconds() // 60)
    penalty = str(minutes + 20)

    boards = f"{APP}/contests/{org}/spring/boards"
    contestant.goto(boards)
    until_shown(contestant, cell(contestant, "IOI", contestant_name, 0), "50")
    expect(rank(contestant, "IOI", contestant_name)()).to_have_text("1")
    expect(rank(contestant, "IOI", rival_name)()).to_have_text("2")
    expect(cell(contestant, "IOI", rival_name, 0)()).to_have_text(re.compile("^(0|—)$"))
    expect(cell(contestant, "ICPC", contestant_name, 0)()).to_have_text("50")
    expect(cell(contestant, "ICPC", contestant_name, 1)()).to_have_text(penalty)
    expect(cell(contestant, "ICPC", contestant_name, 2)()).to_contain_text(
        "1 attempt before"
    )
    expect(board(contestant, "Final")).to_contain_text("Shown from")

    for public in (f"{APP}/contests/{org}/spring", boards):
        visitor.goto(public)
        until_shown(visitor, cell(visitor, "IOI", contestant_name, 0), "50")
        expect(board(visitor, "ICPC")).to_have_count(0)
        expect(board(visitor, "Final")).to_have_count(0)

    submit(rival, 1, SAMPLE)
    assert verdict_of(rival, 1) == "ACCEPTED"
    rival.goto(boards)
    until_shown(rival, cell(rival, "IOI", rival_name, 0), "50")
    expect(rank(rival, "IOI", rival_name)()).to_have_text("1")
    expect(rank(rival, "IOI", contestant_name)()).to_have_text("1")
    expect(rank(rival, "ICPC", rival_name)()).to_have_text("1")
    expect(rank(rival, "ICPC", contestant_name)()).to_have_text("2")

    # A close is refused before a submission already made, and the field
    # takes whole minutes: the task closes at the first minute after the
    # last submission, once that minute has come.
    reveal = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    time.sleep(max(0.0, (reveal - datetime.now(UTC)).total_seconds()) + 1)
    organiser.goto(contest_page)
    settings = open_settings(organiser, "Contest settings")
    settings.get_by_label("A closes", exact=True).fill(local(reveal))
    settings.get_by_role("button", name="Save settings").click()
    expect(organiser.get_by_text(re.compile(r"Saved as version"))).to_be_visible()

    contestant.goto(boards)
    until_shown(contestant, cell(contestant, "IOI", contestant_name, 0), "100")
    expect(cell(contestant, "ICPC", contestant_name, 0)()).to_have_text("100")
    expect(cell(contestant, "ICPC", contestant_name, 1)()).to_have_text(penalty)
    until_shown(contestant, cell(contestant, "Final", contestant_name, 0), "50")

    visitor.goto(boards)
    until_shown(visitor, cell(visitor, "IOI", contestant_name, 0), "100")
    expect(rank(visitor, "IOI", rival_name)()).to_have_text("1")
