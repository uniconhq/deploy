"""A C++ solution that does not compile, graded on the running compose
stack through a pipeline the forge builds, on the dev agent.

An organiser makes an org, a contest and a task on `unicon/classic@v5`
through the API: two runs, every run and the compile log shown at once.
An entrant submits C++ that names a variable it never declares. exec
compiles it once for the step, in a box of its own, and runs nothing: every
run ends `compile_error`, the grading says it did not compile, and the
entrant's page says "Did not compile" on the submission's row and shows the
compiler's log, which names the variable, on the submission's own page.
"""

from __future__ import annotations

import re
import secrets

from playwright.sync_api import Browser, expect

from tests.e2e.api import (
    Caller,
    contest_yaml,
    graded,
    let_in,
    open_once_published,
    save_task,
    submit,
    write_contest,
)
from tests.e2e.stack import (
    APP,
    create_account,
    needs_stack,
    open_page,
    sign_in,
    status_of,
)

pytestmark = [needs_stack]

PROGRAM = b"""\
#include <iostream>

int main() {
    long long a, b;
    std::cin >> a >> b;
    std::cout << a + b + never_declared << "\\n";
}
"""
"""Adds two numbers and a name it never declares, so g++ refuses it."""

TASK = """\
name: Sum
workflow: unicon/classic@v5
inputs:
  code: {entrant: {label: Your solution, max_size: 1MB}}
  language: cpp
  time_limit: 1
  memory_limit: 256
repeat:
  - inputs: [input, answer]
    tuples:
      - [tests/1.in, tests/1.ans]
      - [tests/2.in, tests/2.ans]
scoring:
  values:
    passed: {block: sum, over: {runs: all}, of: {run: [all, passed]}}
  show:
    now: [passed, all, log]
submissions: {max: 50, rate: {count: 1, per: 5}}
"""


def task_files() -> dict[str, str]:
    return {
        "task.yaml": TASK,
        "statement.md": "# Sum\n\nAdd two numbers.\n",
        "tests/1.in": "1 2\n",
        "tests/1.ans": "3\n",
        "tests/2.in": "3 4\n",
        "tests/2.ans": "7\n",
    }


def test_code_that_does_not_compile_says_so_with_its_log(browser: Browser) -> None:
    suffix = secrets.token_hex(3)
    org = f"cmp{suffix}"
    organiser = Caller(browser, f"cmporg{suffix}")
    entrant_name = f"cmpent{suffix}"
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    sign_in(page, entrant_name, create_account(entrant_name))
    entrant = Caller.of(page)

    organiser.send("POST", "/orgs", {"name": org})
    contest = f"/orgs/{org}/contests/broken"
    organiser.send("POST", f"/orgs/{org}/contests", {"name": "broken"})
    organiser.send("POST", f"{contest}/tasks", {"name": "sum"})
    write_contest(organiser, contest, contest_yaml("Broken", "sum", "draft"))
    task = f"{contest}/tasks/sum"
    save_task(organiser, task, task_files())
    open_once_published(organiser, contest, contest_yaml("Broken", "sum", "published"))
    let_in(organiser, entrant, contest)

    number = submit(entrant, task, "main.cpp", PROGRAM)
    grading = graded(entrant, task, number)

    assert grading["status"] == "graded", grading
    assert grading["compiled"] == 0, grading
    assert [run["verdict"] for run in grading["runs"]] == ["compile_error"] * 2

    open_page(page, f"{APP}/contests/{org}/broken/tasks/sum")
    assert status_of(page, number) == "GRADED"
    row = (
        page.get_by_role("table", name="Your submissions")
        .get_by_role("row")
        .filter(has=page.get_by_role("link", name=f"#{number}", exact=True))
    )
    expect(row).to_contain_text("Did not compile")

    open_page(page, f"{APP}/contests/{org}/broken/tasks/sum?submission={number}")
    page.get_by_role("button", name=re.compile(r"^Show (Compile )?log$")).click()
    expect(page.get_by_label(re.compile(r"^(Compile )?log$"))).to_contain_text(
        "never_declared"
    )
