"""What the CI holds for a grading on the running stack: one pipeline with
one workflow, the clone, the steps one after another and the report; a
generator run in each grading rather than at the save; and a regrade after
a checker change as a restart of the pipeline before, graded by the new
checker.

Each test makes its own org, workflow, contest and task through the API,
grades on the dev agent, and reads the pipelines from the CI's API as its
administrator (`tests/e2e/ci.py`).
"""

from __future__ import annotations

import secrets
import subprocess
import time
from typing import Any

from playwright.sync_api import Browser

from tests.e2e import ci
from tests.e2e.api import (
    API,
    ORIGIN,
    Caller,
    contest_yaml,
    graded,
    let_in,
    open_once_published,
    save_task,
    submit,
    token_of,
    write_contest,
)
from tests.e2e.stack import COMPOSE, DEPLOY, SAMPLE, VERDICT_TIMEOUT_MS, needs_stack

pytestmark = needs_stack

SHOW = """\
scoring:
  values:
    passed: {block: sum, over: {runs: all}, of: {run: [all, passed]}}
  show:
    always:
      values: [passed]
      runs:
        - {of: all, fields: [verdict, passed, score]}
submissions: {max: 50, rate: {count: 1, per: 5}}
"""

CLASSIC_TASK = (
    """\
name: Sum
workflow: unicon/classic@v6
inputs:
  code: {entrant: {label: Your solution, max_size: 1MB}}
  language: python
  time_limit: 5
  memory_limit: 64
repeat:
  - inputs: [input, answer]
    tuples:
      - [tests/1.in, tests/1.ans]
      - [tests/2.in, tests/2.ans]
"""
    + SHOW
)

GENERATED = """\
inputs:
  code: {type: folder, title: Solution}
  language: {type: enum, options: [python], title: Language}
  seed: {type: file, title: Seed}

steps:
  - id: generate
    use: unicon/exec@v4
    with:
      source: ${{ workflow.generate/ }}
      language: python
      stdin: ${{ inputs.seed }}
      time_limit: 3
      memory_limit: 256
  - id: reference
    use: unicon/exec@v4
    with:
      source: ${{ workflow.reference/ }}
      language: python
      stdin: ${{ steps.generate.output }}
      time_limit: 3
      memory_limit: 256
  - id: judge
    use: unicon/classic@v6
    with:
      code: ${{ inputs.code }}
      language: ${{ inputs.language }}
      input: ${{ steps.generate.output }}
      answer: ${{ steps.reference.output }}
      time_limit: 5
      memory_limit: 256

outputs:
  score: ${{ steps.judge.score }}
"""
"""A test's input made from its seed, and its answer by a reference
solution: neither step reads anything of the entrant's."""

GENERATOR = """\
import random

seed = int(input())
rng = random.Random(seed * 1_000_003 + random.randrange(1_000_000))
print(rng.randrange(1000), rng.randrange(1000))
"""

REFERENCE = "a, b = map(int, input().split())\nprint(a + b)\n"

JUDGED = """\
inputs:
  code: {type: folder, title: Solution}
  language: {type: enum, options: [python], title: Language}
  input: {type: file, title: Input}
  answer: {type: file, title: Answer}

steps:
  - id: solution
    use: unicon/exec@v4
    with:
      source: ${{ inputs.code }}
      language: ${{ inputs.language }}
      stdin: ${{ inputs.input }}
      time_limit: 5
      memory_limit: 256
  - id: compare
    use: unicon/exec@v4
    with:
      source: ${{ workflow.checker/ }}
      language: python
      manifest: ${{ workflow.checker/program.yaml }}
      time_limit: 3
      memory_limit: 256
      files:
        output: ${{ steps.solution.output }}
        answer: ${{ inputs.answer }}

outputs:
  score: ${{ steps.compare.score }}
"""

MANIFEST = "reports:\n  score: {better: higher, at_least: 0, at_most: 1}\n"

MATCHING = """\
import os
from pathlib import Path

report = Path(os.environ["UNICON_REPORT"])
if Path("output").read_text().split() == Path("answer").read_text().split():
    (report / "score").write_text("1\\n")
    (report / "verdict").write_text("0\\n")
else:
    (report / "verdict").write_text("1\\n")
"""

REFUSING = """\
import os
from pathlib import Path

(Path(os.environ["UNICON_REPORT"]) / "verdict").write_text("1\\n")
"""
"""A checker that finds every output wrong."""


def _contest(browser: Browser, prefix: str) -> tuple[Caller, Caller, str, str]:
    suffix = secrets.token_hex(3)
    org = f"{prefix}{suffix}"
    organiser = Caller(browser, f"{prefix}org{suffix}")
    entrant = Caller(browser, f"{prefix}ent{suffix}")
    organiser.send("POST", "/orgs", {"name": org})
    organiser.send("POST", f"/orgs/{org}/contests", {"name": "graded"})
    return organiser, entrant, org, f"/orgs/{org}/contests/graded"


def _open(
    organiser: Caller, entrant: Caller, contest: str, files: dict[str, str]
) -> str:
    organiser.send("POST", f"{contest}/tasks", {"name": "sum"})
    write_contest(organiser, contest, contest_yaml("Graded", "sum", "draft"))
    task = f"{contest}/tasks/sum"
    save_task(organiser, task, files)
    open_once_published(organiser, contest, contest_yaml("Graded", "sum", "published"))
    let_in(organiser, entrant, contest)
    return task


def _workflow(organiser: Caller, org: str, name: str, files: dict[str, str]) -> str:
    """The org's workflow `name`, its v1 published with `files`; its ref."""
    path = f"/workflows/{org}/{name}"
    organiser.send("POST", "/workflows", {"owner": org, "name": name})
    _publish(organiser, path, files, "v1")
    return f"{org}/{name}"


def _publish(organiser: Caller, path: str, files: dict[str, str], version: str) -> None:
    draft = organiser.get(f"{path}/draft")
    changes = [
        {
            "path": name,
            "token": token_of(organiser, f"{path}/files/{name}"),
            "encoding": "utf-8",
            "content": text,
        }
        for name, text in files.items()
        if name != "workflow.yaml"
    ]
    body = {
        "content": files["workflow.yaml"],
        "token": draft["token"],
        "changes": changes,
    }
    organiser.send("PUT", f"{path}/draft", body)
    organiser.send("POST", f"{path}/versions", {"version": version})


def _gradings(organiser: Caller, task: str, number: int) -> list[dict[str, Any]]:
    """Submission `number`'s attempts, oldest first."""
    rows = [
        entry["grading"]
        for entry in organiser.get(f"{task}/gradings")
        if entry["grading"]["submission_number"] == number
    ]
    return sorted(rows, key=lambda row: int(row["attempt"]))


def _one_pipeline(grading: str) -> dict[str, Any]:
    with ci.client() as client:
        [pipeline] = ci.pipelines_of(client, grading)
    return pipeline


def _sums(task: str, runs: int) -> dict[str, str]:
    files = {"task.yaml": task, "statement.md": "# Sum\n\nAdd two numbers.\n"}
    for run in range(1, runs + 1):
        files[f"tests/{run}.in"] = f"{run} {run * 10}\n"
        files[f"tests/{run}.ans"] = f"{run * 11}\n"
    return files


def test_a_submission_is_graded_by_one_pipeline_with_one_workflow(
    browser: Browser,
) -> None:
    organiser, entrant, _, contest = _contest(browser, "one")
    task = _open(organiser, entrant, contest, _sums(CLASSIC_TASK, 2))

    number = submit(entrant, task, "sum.py", SAMPLE.encode())
    assert graded(entrant, task, number)["status"] == "graded"

    [row] = _gradings(organiser, task, number)
    pipeline = _one_pipeline(row["id"])
    assert pipeline["status"] == "success", pipeline
    assert ci.steps_of(pipeline) == {"grading": ["clone", "s1", "s2", "report"]}


def test_a_generator_runs_in_each_grading(browser: Browser) -> None:
    organiser, entrant, org, contest = _contest(browser, "gen")
    ref = _workflow(
        organiser,
        org,
        "generated",
        {
            "workflow.yaml": GENERATED,
            "generate/generate.py": GENERATOR,
            "reference/reference.py": REFERENCE,
        },
    )
    task_yaml = (
        f"name: Sum\nworkflow: {ref}@v1\n"
        "inputs:\n  code: {entrant: {label: Your solution, max_size: 1MB}}\n"
        "  language: python\n"
        "repeat:\n  - inputs: [seed]\n    tuples:\n      - [tests/1.seed]\n"
        "      - [tests/2.seed]\n" + SHOW
    )
    task = _open(
        organiser,
        entrant,
        contest,
        {
            "task.yaml": task_yaml,
            "statement.md": "# Sum\n\nAdd two numbers.\n",
            "tests/1.seed": "1\n",
            "tests/2.seed": "2\n",
        },
    )

    for _ in range(2):
        number = submit(entrant, task, "sum.py", SAMPLE.encode())
        grading = graded(entrant, task, number)
        assert grading["status"] == "graded", grading
        assert {run["verdict"] for run in grading["runs"]} == {"accepted"}, grading
        [row] = _gradings(organiser, task, number)
        pipeline = _one_pipeline(row["id"])
        # generate, reference, then the judge's solution and compare.
        assert ci.steps_of(pipeline) == {
            "grading": ["clone", "s1", "s2", "s3", "s4", "report"]
        }, pipeline
        states = {
            step["name"]: step["state"] for step in pipeline["workflows"][0]["children"]
        }
        assert set(states.values()) == {"success"}, states
        time.sleep(5)


def test_a_regrade_after_a_checker_change_restarts_the_pipeline_before(
    browser: Browser,
) -> None:
    organiser, entrant, org, contest = _contest(browser, "chk")
    files = {
        "workflow.yaml": JUDGED,
        "checker/check.py": MATCHING,
        "checker/program.yaml": MANIFEST,
    }
    ref = _workflow(organiser, org, "judged", files)
    task_yaml = (
        f"name: Sum\nworkflow: {ref}@v1\n"
        "inputs:\n  code: {entrant: {label: Your solution, max_size: 1MB}}\n"
        "  language: python\n"
        "repeat:\n  - inputs: [input, answer]\n    tuples:\n"
        "      - [tests/1.in, tests/1.ans]\n      - [tests/2.in, tests/2.ans]\n" + SHOW
    )
    task = _open(organiser, entrant, contest, _sums(task_yaml, 2))
    number = submit(entrant, task, "sum.py", SAMPLE.encode())
    first = graded(entrant, task, number)
    assert {run["verdict"] for run in first["runs"]} == {"accepted"}, first

    _publish(
        organiser,
        f"/workflows/{ref}",
        {**files, "checker/check.py": REFUSING},
        "v2",
    )
    _resave(organiser, task, {"task.yaml": task_yaml.replace("@v1", "@v2")})

    deadline = time.monotonic() + VERDICT_TIMEOUT_MS / 1000
    while True:
        rows = _gradings(organiser, task, number)
        if len(rows) == 2 and rows[1]["status"] not in (
            "queued",
            "dispatched",
            "running",
        ):
            break
        assert time.monotonic() < deadline, rows
        time.sleep(3)
    before, after = rows
    assert after["status"] == "done", after
    verdicts = {run["verdict"] for run in after["result"]["runs"]}
    assert verdicts == {"wrong_answer"}, after

    with ci.client() as client:
        old, new = ci.pipelines_of(client, before["id"])
    assert new["parent"] == old["number"], (old["number"], new["parent"])
    assert ci.steps_of(new) == {"grading": ["clone", "s1", "s2", "report"]}


def _resave(organiser: Caller, task: str, files: dict[str, str]) -> None:
    """Save `files` over the task, confirming the regrade it asks for."""
    changes = [
        {
            "path": path,
            "content": text,
            "token": token_of(organiser, f"{task}/files/{path}"),
        }
        for path, text in files.items()
    ]
    body: dict[str, Any] = {
        "changes": changes,
        "confirm": True,
        "message": "Move the pin",
    }
    asked = organiser.request.fetch(
        f"{API}{task}/save", method="POST", data=body, headers=ORIGIN
    )
    assert asked.status == 409, asked.text()
    body["asked"] = asked.json()["asked"]
    saved = organiser.send("POST", f"{task}/save", body)
    deadline = time.monotonic() + VERDICT_TIMEOUT_MS / 1000
    while saved.get("status") == "preparing":
        assert time.monotonic() < deadline, "the task's save was not prepared in time"
        time.sleep(1)
        state = organiser.get(f"{task}/preparation")
        if state["status"] == "ready":
            saved = state["result"]
    assert saved.get("number") == 2, saved


SLOW = "import time\n\ntime.sleep(25)\n" + SAMPLE
"""A solution slow enough for its grading's agent to be killed meanwhile."""
OVERDUE_S = 20 * 60


def _agent(*command: str) -> None:
    subprocess.run(
        [*COMPOSE, "--profile", "agent", *command, "woodpecker-agent"],
        cwd=DEPLOY,
        check=True,
        capture_output=True,
    )


def test_a_killed_agent_gives_a_restart_whose_parent_is_the_old_pipeline(
    browser: Browser,
) -> None:
    organiser, entrant, _, contest = _contest(browser, "kill")
    slow_task = CLASSIC_TASK.replace("time_limit: 5", "time_limit: 40")
    task = _open(organiser, entrant, contest, _sums(slow_task, 2))
    number = submit(entrant, task, "sum.py", SLOW.encode())

    deadline = time.monotonic() + VERDICT_TIMEOUT_MS / 1000
    with ci.client() as client:
        while True:
            rows = _gradings(organiser, task, number)
            found = ci.pipelines_of(client, rows[0]["id"]) if rows else []
            steps = {
                step["name"]: step["state"]
                for pipeline in found
                for workflow in pipeline.get("workflows") or []
                for step in workflow.get("children") or []
            }
            if steps.get("s1") == "running":
                break
            assert time.monotonic() < deadline, steps
            time.sleep(1)
    _agent("kill")
    try:
        time.sleep(5)
    finally:
        _agent("up", "-d")

    # Woodpecker 3.18.1 drops the dead agent's workflow and leaves its
    # pipeline running, so the grading ends only at its deadline, which a
    # read finds; the platform's own retry then restarts that pipeline.
    deadline = time.monotonic() + OVERDUE_S
    while True:
        rows = _gradings(organiser, task, number)
        if len(rows) > 1 and rows[-1]["status"] == "done":
            break
        assert time.monotonic() < deadline, rows
        time.sleep(10)
    first, *_, last = rows
    assert last["automatic"], last
    assert {run["verdict"] for run in last["result"]["runs"]} == {"accepted"}, last
    with ci.client() as client:
        pipelines = ci.pipelines_of(client, first["id"])
    old, new = pipelines[0], pipelines[-1]
    assert new["parent"] == old["number"], [
        (pipeline["number"], pipeline["parent"], pipeline["status"])
        for pipeline in pipelines
    ]


def test_the_checkout_is_in_its_own_folder(browser: Browser) -> None:
    # #646: the submission is cloned into submission/, beside the steps' folders.
    organiser, entrant, _, contest = _contest(browser, "own")
    task = _open(organiser, entrant, contest, _sums(CLASSIC_TASK, 2))
    number = submit(entrant, task, "sum.py", SAMPLE.encode())
    grading = graded(entrant, task, number)
    assert {run["verdict"] for run in grading["runs"]} == {"accepted"}, grading

    [row] = _gradings(organiser, task, number)
    pipeline = _one_pipeline(row["id"])
    deadline = time.monotonic() + 30
    with ci.client() as client:
        # The clone's log was once seen empty just after the grading ended.
        while not (cloned := ci.log_of(client, pipeline, "clone")):
            assert time.monotonic() < deadline
            time.sleep(1)
    assert "/unicon-dispatch/submission/.git/" in cloned, cloned
