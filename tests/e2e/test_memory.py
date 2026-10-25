"""One run over its memory limit, graded on the running compose stack in
the same exec step as runs that pass, through a pipeline the forge builds,
on a grading machine.

An organiser makes an org, a contest and a task on `unicon/classic@v3`
through the API: four runs of an entrant's C program, each reading two
numbers to add and how many megabytes to hold first. Three hold nothing;
one holds 512 MB under the task's 64 MB. The entrant submits the program.
The run over its memory ends `memory_limit`, and the other three are still
compared with their answers and accepted: the kernel killing a process in
one box neither fails the step nor stops the runs beside it.

That holds on a grading machine whose cgroup tree counts a kill only in
the group it happened in (`memory_localevents`, which the setup script
mounts it with). Without it, the step container's own `memory.events`
counts the box's kill, Docker marks the container OOM-killed, and
Woodpecker fails the whole step. The test needs a grading machine with
exec's shape, so, like the escape test, it is skipped unless
`UNICON_E2E_ESCAPE` is set, besides `UNICON_E2E_URL`. It is skipped too
when `UNICON_E2E_SHARED_KILLS` says the machine counts kills up the tree,
as the stand-in grading machine in a container does: its cgroup namespace
is private, so it cannot remount the tree with `memory_localevents`.
"""

from __future__ import annotations

import os
import secrets

import pytest
from playwright.sync_api import Browser

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
from tests.e2e.stack import needs_stack

pytestmark = [
    needs_stack,
    pytest.mark.skipif(
        not os.environ.get("UNICON_E2E_ESCAPE"),
        reason="UNICON_E2E_ESCAPE says no grading machine with exec's shape is up",
    ),
    pytest.mark.skipif(
        bool(os.environ.get("UNICON_E2E_SHARED_KILLS")),
        reason=(
            "UNICON_E2E_SHARED_KILLS says the machine counts a box's kill in "
            "the step's cgroup, so Woodpecker fails the step"
        ),
    ),
]

PROGRAM = b"""\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    long a, b, megabytes;
    if (scanf("%ld %ld %ld", &a, &b, &megabytes) != 3) return 1;
    for (long i = 0; i < megabytes; i++) {
        char *held = malloc(1 << 20);
        if (held == NULL) return 2;
        memset(held, 1, 1 << 20);
    }
    printf("%ld\\n", a + b);
    return 0;
}
"""
"""Adds two numbers after holding as many megabytes as it is told, each
written to so the memory is really taken."""

RUNS = {"1": (1, 2, 0), "2": (3, 4, 0), "3": (5, 6, 512), "4": (7, 8, 0)}
"""Each run's two numbers and the megabytes it holds; run 3 holds eight
times the task's memory limit."""

TASK = """\
name: Sum
workflow: unicon/classic@v3
inputs:
  code: {entrant: {label: Your solution, max_size: 1MB}}
  language: c
  time_limit: 5
  memory_limit: 64
repeat:
  - inputs: [input, answer]
    tuples:
      - [tests/1.in, tests/1.ans]
      - [tests/2.in, tests/2.ans]
      - [tests/3.in, tests/3.ans]
      - [tests/4.in, tests/4.ans]
scoring:
  values:
    passed: {block: sum_runs, over: {runs: all}, of: {run: [all, passed]}}
  show:
    now: [passed, all, tests/]
submissions: {max: 50, rate: {count: 1, per: 5}}
"""


def task_files() -> dict[str, str]:
    files = {"task.yaml": TASK, "statement.md": "# Sum\n\nAdd two numbers.\n"}
    for name, (a, b, megabytes) in RUNS.items():
        files[f"tests/{name}.in"] = f"{a} {b} {megabytes}\n"
        files[f"tests/{name}.ans"] = f"{a + b}\n"
    return files


def test_a_run_over_its_memory_ends_alone(browser: Browser) -> None:
    suffix = secrets.token_hex(3)
    org = f"mem{suffix}"
    organiser = Caller(browser, f"memorg{suffix}")
    entrant = Caller(browser, f"ment{suffix}")

    organiser.send("POST", "/orgs", {"name": org})
    contest = f"/orgs/{org}/contests/memory"
    organiser.send("POST", f"/orgs/{org}/contests", {"name": "memory"})
    organiser.send("POST", f"{contest}/tasks", {"name": "sum"})
    write_contest(organiser, contest, contest_yaml("Memory", "sum", "draft"))
    task = f"{contest}/tasks/sum"
    save_task(organiser, task, task_files())
    open_once_published(organiser, contest, contest_yaml("Memory", "sum", "published"))
    let_in(organiser, entrant, contest)

    grading = graded(entrant, task, submit(entrant, task, "sum.c", PROGRAM))

    assert grading["status"] == "graded", grading
    ended = {
        run["key"]["input"]: (run["verdict"], run["reports"].get("score"))
        for run in grading["runs"]
    }
    assert ended == {
        "tests/1.in": ("accepted", 1),
        "tests/2.in": ("accepted", 1),
        "tests/3.in": ("memory_limit", None),
        "tests/4.in": ("accepted", 1),
    }, grading
