"""Rank one contest's gradings on three leaderboards on the running compose
stack, read before and after the task's reveal by two entrants, a visitor
and an organiser.

The organiser gives the starter task a second test group, `final`, shown
only once the task closes, so `main` and `final` each carry 50 of the
task's 100 points, writes its statement, and builds the contest's three
leaderboards on its Leaderboards page: `IOI`, on points, shown to everyone;
`ICPC`, on points and then penalty at 20 minutes an earlier attempt, shown
to entrants; and `Final`, over the results shown once the task closes,
counting the submission each entrant marked, shown to entrants. Two people
register and are approved; the second, the rival, has a browser whose
clock is three hours fast, and reads the task's countdown by Unicon's clock
all the same.

The entrant sends a wrong solution and then the right one and marks the
wrong one: the organiser's Final, once every result is shown, counts it,
0, not the better one. Moving the mark to the right one gives it its 50.
Before the rival submits, they are on IOI as a bare row below. Before the
reveal every leaderboard counts only what is shown: IOI and ICPC give the
right solution `main`'s 50 points, ICPC with a penalty of the minutes from
the start to it and 20 for the wrong attempt before it, exactly; Final
shows nothing yet; a visitor, signed out, sees IOI and not ICPC, on the
contest's own page and on its leaderboards; the organiser sees Final now
as the entrant does. The rival's right solution ties them on IOI and,
with no wrong attempt, puts them first on ICPC. The organiser closes the
task, and every leaderboard counts `final` too.
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
    STATEMENT,
    VERDICT_TIMEOUT_MS,
    WRONG,
    add_test,
    approve,
    contest_settings,
    create,
    create_account,
    create_org,
    group_rule,
    local,
    needs_stack,
    open_contest,
    register,
    save_task,
    sign_in,
    submission_rate,
    submit,
    task_timeline,
    verdict_of,
    write_statement,
)

pytestmark = [needs_stack]

EVERY_RESULT = "once every result is shown"


def until_shown(
    page: Page, find: Callable[[], Locator], text: str | re.Pattern[str]
) -> None:
    """Read `find` again, reloading the page, until it shows `text`: a
    leaderboard is read when its page loads, and a grading lands on it when
    it finishes.
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


def leaderboard(page: Page, name: str) -> Locator:
    return page.get_by_role("region", name=name, exact=True)


def row_of(page: Page, table: str, who: str) -> Locator:
    return (
        page.get_by_role("table", name=table, exact=True)
        .get_by_role("row")
        .filter(
            has=page.get_by_role("rowheader", name=re.compile(re.escape(who), re.I))
        )
    )


def cell(page: Page, table: str, who: str, index: int) -> Callable[[], Locator]:
    """The `index`th data cell of `who`'s row on `table`, after the rank."""
    return lambda: row_of(page, table, who).get_by_role("cell").nth(index + 1)


def rank(page: Page, table: str, who: str) -> Callable[[], Locator]:
    """The rank on `who`'s row of `table`."""
    return lambda: row_of(page, table, who).get_by_role("cell").first


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


def leaderboards(page: Page, org: str) -> None:
    """IOI, ICPC and Final, built on the contest's Leaderboards page."""
    page.goto(f"{APP}/orgs/{org}/contests/spring/boards")
    form = page.get_by_role("form", name="Leaderboards")
    first = form.get_by_role("group", name="Standings", exact=True)
    first.get_by_role("textbox", name="Name", exact=True).fill("IOI")
    ioi = form.get_by_role("group", name="IOI", exact=True)
    ioi.get_by_role("radiogroup", name="Shown to").get_by_role(
        "radio", name="Everyone who can see the contest"
    ).check()

    form.get_by_role("button", name="Add a leaderboard").click()
    added = form.get_by_role("group", name=re.compile("^Leaderboard 2"))
    added.get_by_role("textbox", name="Name", exact=True).fill("ICPC")
    icpc = form.get_by_role("group", name="ICPC", exact=True)
    icpc.get_by_role("radiogroup", name="Shown to").get_by_role(
        "radio", name="Entrants", exact=True
    ).check()
    form.get_by_role("button", name="Add a way to rank ICPC").click()
    then = icpc.get_by_role("group", name="Way to rank 2", exact=True)
    expect(then.get_by_role("combobox", name="Way to rank 2")).to_have_value("penalty")
    then.get_by_role("textbox", name="Minutes per earlier attempt").fill("20")

    form.get_by_role("button", name="Add a leaderboard").click()
    added = form.get_by_role("group", name=re.compile("^Leaderboard 3"))
    added.get_by_role("textbox", name="Name", exact=True).fill("Final")
    final = form.get_by_role("group", name="Final", exact=True)
    final.get_by_role("radiogroup", name="Shown to").get_by_role(
        "radio", name="Entrants", exact=True
    ).check()
    final.get_by_role("radiogroup", name="Counts", exact=True).get_by_role(
        "radio", name="Results shown once each task closes"
    ).check()
    final.get_by_role("radiogroup", name="Which submission counts").get_by_role(
        "radio", name="Best of the submissions an entrant marks"
    ).check()
    form.get_by_role("button", name="Save leaderboards").click()
    expect(page.get_by_text("Saved.", exact=True)).to_be_visible(
        timeout=CREATE_TIMEOUT_MS
    )


def test_three_leaderboards_count_what_is_shown_before_and_after_the_reveal(
    browser: Browser,
) -> None:
    stamp = secrets.token_hex(3)
    organiser_name, entrant_name = f"e2e-o-{stamp}", f"e2e-c-{stamp}"
    rival_name = f"e2e-r-{stamp}"
    org = f"e2e-org-{stamp}"
    organiser_password = create_account(organiser_name)
    entrant_password = create_account(entrant_name)
    rival_password = create_account(rival_name)

    viewport: ViewportSize = {"width": 1280, "height": 900}
    organiser = browser.new_context(viewport=viewport).new_page()
    entrant = browser.new_context(viewport=viewport).new_page()
    visitor = browser.new_context(viewport=viewport).new_page()
    fast = browser.new_context(viewport=viewport)
    fast.clock.install(time=datetime.now(UTC) + timedelta(hours=3))
    rival = fast.new_page()
    for page in (organiser, entrant, visitor, rival):
        page.set_default_timeout(30_000)

    sign_in(organiser, organiser_name, organiser_password)
    create_org(organiser, org)
    create(organiser, "spring", "Create contest", "New contest")
    create(organiser, "sum", "Create task", "New task")
    task_page = organiser.url

    add_test(organiser, "final", {"input": b"5 7\n", "answer": b"12\n"}, new_group=True)
    group_rule(organiser, "main", "Points for each test passed", "50")
    group_rule(organiser, "final", "Points for each test passed", "50")
    organiser.get_by_role("group", name="final", exact=True).get_by_role(
        "combobox", name="When entrants see results"
    ).select_option("after_close")
    submission_rate(organiser, 10, 60)
    save_task(organiser)
    write_statement(organiser, task_page, STATEMENT)

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    contest_settings(
        organiser,
        org,
        "spring",
        Name=f"Spring {stamp}",
        Start=local(now - timedelta(hours=1)),
        End=local(now + timedelta(hours=3)),
    )
    task_timeline(organiser, org, "spring", "A · sum", closes=now + timedelta(hours=2))
    leaderboards(organiser, org)

    organiser.goto(f"{APP}/orgs/{org}/contests/spring")
    organiser.get_by_role("button", name="Change who sees it").click()
    seen = organiser.get_by_role("dialog", name="Who sees it")
    seen.get_by_role("radio", name="Anyone, signed in or not").check()
    seen.get_by_role("button", name="Save").click()
    expect(seen).to_be_hidden(timeout=CREATE_TIMEOUT_MS)
    open_contest(organiser, org, "spring")

    sign_in(entrant, entrant_name, entrant_password)
    register(entrant, org, "spring")
    sign_in(rival, rival_name, rival_password)
    register(rival, org, "spring")
    approve(organiser, org, "spring", entrant_name)
    approve(organiser, org, "spring", rival_name)

    for page in (entrant, rival):
        expect(page.get_by_text("You are in")).to_be_visible(timeout=CREATE_TIMEOUT_MS)
        page.goto(f"{APP}/contests/{org}/spring/tasks/sum")
        expect(page.get_by_role("timer", name="Task countdown")).to_contain_text(
            re.compile(r"Closes in 1h [345]\dm")
        )

    submit(entrant, 1, WRONG)
    assert verdict_of(entrant, 1) == "WRONG ANSWER"
    submit(entrant, 2, SAMPLE)
    assert verdict_of(entrant, 2) == "ACCEPTED"
    toggle_mark(entrant, 1, checked=True)

    organiser.goto(f"{APP}/orgs/{org}/contests/spring/boards")
    until_shown(
        organiser, cell(organiser, f"IOI, {EVERY_RESULT}", entrant_name, 0), "100"
    )
    expect(cell(organiser, f"Final, {EVERY_RESULT}", entrant_name, 0)()).to_have_text(
        "0"
    )

    toggle_mark(entrant, 1, checked=False)
    toggle_mark(entrant, 2, checked=True)

    organiser.reload()
    expect(leaderboard(organiser, "Final, now")).to_contain_text("Shown from")
    until_shown(
        organiser, cell(organiser, f"Final, {EVERY_RESULT}", entrant_name, 0), "50"
    )
    expect(cell(organiser, "IOI, now", entrant_name, 0)()).to_have_text("50")

    # Whole minutes from the contest's start to the right solution, and 20
    # for the wrong attempt before it.
    start = now - timedelta(hours=1)
    minutes = int((submitted_at(entrant, org, 2) - start).total_seconds() // 60)
    penalty = str(minutes + 20)

    public_leaderboards = f"{APP}/contests/{org}/spring/boards"
    entrant.goto(public_leaderboards)
    until_shown(entrant, cell(entrant, "IOI", entrant_name, 0), "50")
    expect(rank(entrant, "IOI", entrant_name)()).to_have_text("1")
    expect(rank(entrant, "IOI", rival_name)()).to_have_text("2")
    expect(cell(entrant, "IOI", rival_name, 0)()).to_have_text(re.compile("^(0|—)$"))
    expect(cell(entrant, "ICPC", entrant_name, 0)()).to_have_text("50")
    expect(cell(entrant, "ICPC", entrant_name, 1)()).to_have_text(penalty)
    expect(cell(entrant, "ICPC", entrant_name, 2)()).to_contain_text("1 attempt before")
    expect(leaderboard(entrant, "Final")).to_contain_text("Shown from")

    for public in (f"{APP}/contests/{org}/spring", public_leaderboards):
        visitor.goto(public)
        until_shown(visitor, cell(visitor, "IOI", entrant_name, 0), "50")
        expect(leaderboard(visitor, "ICPC")).to_have_count(0)
        expect(leaderboard(visitor, "Final")).to_have_count(0)

    submit(rival, 1, SAMPLE)
    assert verdict_of(rival, 1) == "ACCEPTED"
    rival.goto(public_leaderboards)
    until_shown(rival, cell(rival, "IOI", rival_name, 0), "50")
    expect(rank(rival, "IOI", rival_name)()).to_have_text("1")
    expect(rank(rival, "IOI", entrant_name)()).to_have_text("1")
    expect(rank(rival, "ICPC", rival_name)()).to_have_text("1")
    expect(rank(rival, "ICPC", entrant_name)()).to_have_text("2")

    # A close is refused before a submission already made, and the field
    # takes whole minutes: the task closes at the first minute after the
    # last submission, the rival's, once that minute has come.
    last = submitted_at(rival, org, 1)
    reveal = last.replace(second=0, microsecond=0) + timedelta(minutes=1)
    time.sleep(max(0.0, (reveal - datetime.now(UTC)).total_seconds()) + 1)
    task_timeline(organiser, org, "spring", "A · sum", closes=reveal)

    entrant.goto(public_leaderboards)
    until_shown(entrant, cell(entrant, "IOI", entrant_name, 0), "100")
    expect(cell(entrant, "ICPC", entrant_name, 0)()).to_have_text("100")
    expect(cell(entrant, "ICPC", entrant_name, 1)()).to_have_text(penalty)
    until_shown(entrant, cell(entrant, "Final", entrant_name, 0), "50")

    visitor.goto(public_leaderboards)
    until_shown(visitor, cell(visitor, "IOI", entrant_name, 0), "100")
    expect(rank(visitor, "IOI", rival_name)()).to_have_text("1")
