"""Build a workflow in the editor, version it, share it, and grade with it
and with unicon/classic-folder@v1, on the running compose stack, from the
browser.

An author builds a private workflow on the editor's graph, starting from
the starter it is made as: an input and a value of each test declared in
place, the check step taken out and a diff-check added back from the
toolbar's Add step, its ports wired by dragging, one drag refused at the
port with the reason, the run's arguments given text with the new input
written in, and a report entry made from an output. The draft saves with
a port left unwired and its version is refused until it is wired; wired,
saved and published, the version keeps the file's comment. The author
shares it with an organiser, who chooses it for a task: that task's save
writes one `plans/plan.json` in which each per-test step over a primitive
that takes a batch is a single step. A second task, of a program of
several files, grades with unicon/classic-folder@v1. With each task's
statement written, the contest opens, and an entrant's solution to each
grades accepted; the forge takes a run's `result.json` only when
it validates against the runner's `result.schema.json`, and grades
anything else a system error, so an accepted verdict is a result that
validated. Every seeded workflow draws with no wire behind a box, the same
after a reload.

It runs against a stack that is already up with both profiles, `app` and
`agent`, and is skipped unless `UNICON_E2E_URL` names the app. Every name it
makes carries a random suffix, so it runs again on the same stack.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, FloatRect, Locator, Page, ViewportSize, expect

from tests.e2e.stack import (
    APP,
    CREATE_TIMEOUT_MS,
    SAMPLE,
    STATEMENT,
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
    write_statement,
)

pytestmark = [needs_stack]

# The pages write a typographic apostrophe, which ruff would take for a mistyped quote.
DRAFT_FILE = "The draft’s file"  # noqa: RUF001
EACH_TEST = "Each test’s files"  # noqa: RUF001
LIMIT_REFUSED = "This value raises a limit, so the task must give it, not the entrant."

# Every box's place and size and every wire's path, in the graph's own
# coordinates, and each wire that passes behind a box it does not join.
DRAWN = """() => {
  const number = (text) => Number.parseFloat(text);
  const boxes = [...document.querySelectorAll('.react-flow__node-box')].map((node) => {
    const at = /translate\\(([-\\d.]+)px, *([-\\d.]+)px\\)/.exec(node.style.transform);
    return {
      id: node.dataset.id,
      x: number(at[1]), y: number(at[2]),
      w: number(node.style.width), h: number(node.style.height),
    };
  });
  const wires = [...document.querySelectorAll('.react-flow__edge-path')];
  const behind = [];
  for (const wire of wires) {
    const length = wire.getTotalLength();
    for (let step = 0; step <= 80; step += 1) {
      const point = wire.getPointAtLength((length * step) / 80);
      const inside = boxes.find((box) =>
        point.x > box.x + 1 && point.x < box.x + box.w - 1 &&
        point.y > box.y + 1 && point.y < box.y + box.h - 1);
      if (inside) {
        behind.push(`${wire.getAttribute('d')} behind ${inside.id}`);
        break;
      }
    }
  }
  return { boxes, wires: wires.map((wire) => wire.getAttribute('d')), behind };
}"""


def _drawn(page: Page) -> dict[str, Any]:
    page.wait_for_function(
        "() => document.querySelectorAll('.react-flow__edge-path').length > 0"
    )
    found: dict[str, Any] = page.evaluate(DRAWN)
    return found


def _steady(page: Page, handle: Locator) -> FloatRect:
    """Where `handle` stands once it has stopped moving: every edit lays the
    graph out again, which can move a box after the edit is drawn.
    """
    handle.scroll_into_view_if_needed()
    box = handle.bounding_box()
    for _ in range(20):
        page.wait_for_timeout(150)
        again = handle.bounding_box()
        if again == box and again is not None:
            return again
        box = again
    assert box is not None
    return box


def _drag(page: Page, source: Locator, target: Locator) -> None:
    """A wire dragged from one handle to another, the way a mouse draws it."""
    _steady(page, target)
    _steady(page, source)
    source.hover()
    page.mouse.down()
    target.scroll_into_view_if_needed()
    box = _steady(page, target)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=12)
    page.mouse.up()


def _handle(page: Page, node: str, handle: str) -> Locator:
    box = f'.react-flow__node[data-id="{node}"]'
    return page.locator(f'{box} .react-flow__handle[data-handleid="{handle}"]')


def _node_of(page: Page, port: str) -> str:
    """The id of the box whose right-hand handle is `port`."""
    found = page.locator(f'.react-flow__handle[data-handleid="{port}"]').first
    node = found.get_attribute("data-nodeid")
    assert node is not None
    return node


# The draft's file in the View file dialog is CodeMirror, which draws only
# the lines in view; the whole text is its view's document, which CodeMirror
# keeps on the content element's tile.
FILE = """(name) => {
  const label = [...document.querySelectorAll('label')].find(
    (found) => found.textContent.trim() === name);
  const content = document.querySelector('[aria-labelledby="' + label.id + '"]');
  const tile = content.cmTile || content.cmView;
  const view = tile && tile.view;
  if (!view) throw new Error('the file view holds no CodeMirror document');
  return view.state.doc.toString();
}"""


def _file(page: Page) -> str:
    """The draft's file as a save would write it, read from View file."""
    page.get_by_role("toolbar", name="The workflow").get_by_role(
        "button", name="View file"
    ).click()
    dialog = page.get_by_role("dialog", name=DRAFT_FILE)
    expect(dialog).to_be_visible()
    text: str = page.evaluate(FILE, DRAFT_FILE)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    return text


def _until(read: Callable[[], Any], holds: Callable[[Any], bool], what: str) -> None:
    """Read again, a second apart, until what is read holds, for a minute."""
    deadline = time.monotonic() + CREATE_TIMEOUT_MS / 1000
    while True:
        found = read()
        if holds(found):
            return
        assert time.monotonic() < deadline, f"{what}: {found}"
        time.sleep(1)


def _file_shows(page: Page, pattern: str) -> None:
    """Wait until the draft's file holds `pattern`, a regular expression."""
    _until(
        lambda: _file(page), lambda text: re.search(pattern, text) is not None, pattern
    )


def _drawer(page: Page, name: str) -> Locator:
    return page.get_by_role("complementary", name=name)


def _save(page: Page) -> None:
    bar = page.get_by_role("toolbar", name="The workflow")
    bar.get_by_role("button", name="Save", exact=True).click()
    expect(bar.get_by_text("All changes saved.")).to_be_visible()


def test_a_workflow_is_built_versioned_shared_and_graded_with(
    browser: Browser, tmp_path: Path
) -> None:
    stamp = secrets.token_hex(3)
    author_name, organiser_name = f"e2e-wa-{stamp}", f"e2e-wo-{stamp}"
    entrant_name = f"e2e-wc-{stamp}"
    passwords = {
        name: create_account(name)
        for name in (author_name, organiser_name, entrant_name)
    }
    viewport: ViewportSize = {"width": 1600, "height": 1000}
    author, organiser, entrant = (
        browser.new_context(viewport=viewport).new_page() for _ in range(3)
    )
    for page in (author, organiser, entrant):
        page.set_default_timeout(30_000)
    sign_in(author, author_name, passwords[author_name])
    sign_in(organiser, organiser_name, passwords[organiser_name])
    sign_in(entrant, entrant_name, passwords[entrant_name])

    # Every seeded workflow draws with no wire behind a box, and the same
    # after a reload.
    for seeded in (
        "classic?version=v2",
        "classic-folder?version=v1",
    ):
        author.goto(f"{APP}/workflows/unicon/{seeded}")
        expect(author.get_by_role("group", name="Step compile")).to_be_visible()
        first = _drawn(author)
        assert first["behind"] == [], seeded
        author.reload()
        expect(author.get_by_role("group", name="Step compile")).to_be_visible()
        again = _drawn(author)
        assert (again["boxes"], again["wires"]) == (first["boxes"], first["wires"]), (
            seeded
        )

    # The author makes a private workflow, which starts as the starter.
    owner = author_name.lower()
    author.goto(f"{APP}/workflows")
    author.get_by_role("button", name="New workflow").click()
    made = author.get_by_role("dialog", name="New workflow")
    made.get_by_role("radio", name="The starter").check()
    made.get_by_role("textbox", name="Name", exact=True).fill("graded")
    made.get_by_role("button", name="Create workflow").click()
    expect(
        author.get_by_role("heading", name=f"{owner}/graded", level=1)
    ).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    expect(author.get_by_role("group", name="Step check")).to_be_visible()
    comment = f"# {owner}/graded, a workflow."
    assert _file(author).startswith(comment)

    # An input and a value of each test, declared in place.
    editing = author.get_by_role("toolbar", name="Editing")
    editing.get_by_role("button", name="Inputs", exact=True).click()
    adding = _drawer(author, "Inputs").get_by_role("form", name="Add an input")
    adding.get_by_role("textbox", name="New input").fill("seed")
    adding.get_by_role("combobox", name="Of type").select_option("number")
    adding.get_by_role("button", name="Add an input").click()
    editing.get_by_role("button", name=EACH_TEST, exact=True).click()
    adding = _drawer(author, EACH_TEST).get_by_role("form", name="Add to each test")
    adding.get_by_role("textbox", name="New file or value").fill("expected")
    adding.get_by_role("combobox", name="Of type").select_option("file")
    adding.get_by_role("button", name="Add to each test").click()
    author.keyboard.press("Escape")
    _file_shows(author, r"\n  seed: number\n")
    _file_shows(author, r"\n  expected: file\n")

    # The check step out, and a diff-check added back from Add step.
    author.get_by_role("group", name="Step check").get_by_role(
        "button", name=re.compile("^check")
    ).click()
    _drawer(author, "Step check").get_by_role(
        "button", name="Remove the step check"
    ).click()
    expect(author.get_by_role("group", name="Step check")).to_have_count(0)
    editing.get_by_role("button", name="Add step").click()
    author.get_by_role("menuitem", name=re.compile("^Compare with the answer")).click()
    expect(author.get_by_role("group", name="Step diff-check")).to_be_visible()
    added = _drawer(author, "Step diff-check")
    expect(added.get_by_role("radio", name="Once for each test")).to_be_checked()
    author.keyboard.press("Escape")
    expect(added).to_be_hidden()

    # Its output wired by a drag, and a drag a version would refuse, refused.
    _drag(
        author,
        _handle(author, "step:1", "out:output"),
        _handle(author, "step:2", "in:actual"),
    )
    _file_shows(author, r"actual: \$\{\{ steps\.run\.output \}\}")
    _drag(
        author,
        _handle(author, _node_of(author, "out:submission"), "out:submission"),
        _handle(author, "step:1", "in:time_limit"),
    )
    expect(author.get_by_role("alert").filter(has_text=LIMIT_REFUSED)).to_be_visible()
    assert "time_limit: ${{ inputs.time_limit }}" in _file(author)

    # Text with the new input written in, and an output reported.
    author.get_by_role("group", name="Step run").get_by_role(
        "button", name=re.compile("^run")
    ).click()
    panel = _drawer(author, "Step run")
    args = panel.get_by_role("textbox", name=re.compile("^args"))
    args.fill("--seed ")
    args.press("Enter")
    panel.get_by_role("button", name="Write a value into args").click()
    author.get_by_role("menuitem", name="seed from the inputs").click()
    author.keyboard.press("Escape")
    _file_shows(author, r'args: "?--seed \$\{\{ inputs\.seed \}\}"?')
    author.get_by_role("button", name="The output time_ms of run").click()
    author.get_by_role("menuitem", name="Report it").click()
    _file_shows(author, r"time_ms_2: \$\{\{ steps\.run\.time_ms \}\}")

    # The draft saves with diff-check's expected unwired; its version does not.
    problems = author.get_by_role("region", name="Problems")
    expect(problems).to_contain_text(
        "unicon/diff-check@v2 needs a value for its input expected."
    )
    _save(author)
    bar = author.get_by_role("toolbar", name="The workflow")
    bar.get_by_role("button", name="Publish version").click()
    publish = author.get_by_role("dialog", name="Publish v1")
    expect(publish).to_contain_text("One problem stops it")
    expect(publish.get_by_role("button", name="Publish v1")).to_be_disabled()
    author.keyboard.press("Escape")

    # Wired, saved and published; the version keeps the file's comment.
    _drag(
        author,
        _handle(author, _node_of(author, "out:expected"), "out:expected"),
        _handle(author, "step:2", "in:expected"),
    )
    _file_shows(author, r"expected: \$\{\{ test\.expected \}\}")
    _save(author)
    expect(problems).to_contain_text("Nothing to fix")
    bar.get_by_role("button", name="Publish version").click()
    author.get_by_role("dialog", name="Publish v1").get_by_role(
        "button", name="Publish v1"
    ).click()
    expect(author.get_by_text("v1 is published.")).to_be_visible()
    version = author.request.get(f"{APP}/api/v1/workflows/{owner}/graded/versions/v1")
    assert version.ok, version.text()
    content = version.json()["content"]
    assert content.startswith(comment)
    assert "--seed ${{ inputs.seed }}" in content

    # Shared with the organiser.
    bar.get_by_role("button", name="Share").click()
    sharing = author.get_by_role("region", name="Who can see it")
    shared = sharing.get_by_role("radio", name=re.compile("^Shared"))
    shared.click()
    expect(shared).to_be_checked(timeout=CREATE_TIMEOUT_MS)
    expect(sharing.get_by_text("Shared with nobody yet")).to_be_visible()
    sharing.get_by_role("textbox", name="Username").fill(organiser_name)
    sharing.get_by_role("button", name="Share", exact=True).click()
    expect(sharing.get_by_role("list", name="Shared with")).to_contain_text(
        organiser_name.lower()
    )
    author.keyboard.press("Escape")

    # The organiser chooses it for a task, whose save writes one plan.
    org = f"e2e-wf-{stamp}"
    create_org(organiser, org)
    create(organiser, "spring", "Create contest", "New contest")
    contest_page = organiser.url
    create(organiser, "made", "Create task", "New task")
    made_page = organiser.url
    workflow = organiser.get_by_role("region", name="Workflow")
    workflow.get_by_role("combobox", name="Workflow").select_option(
        label=f"{owner}/graded"
    )
    expect(workflow.get_by_role("combobox", name="Version")).to_have_value("v1")
    limits = organiser.get_by_role("region", name="Limits and settings")
    limits.get_by_role("textbox", name="seed", exact=True).fill("7")
    replace_test(organiser, "main", "1", {"expected": b"3\n"})
    submission_rate(organiser, 10, 60)
    save_task(organiser)
    made_task = f"{APP}/api/v1/orgs/{org}/contests/spring/tasks/made"
    task_yaml = organiser.request.get(f"{made_task}/files/task.yaml").json()["content"]
    assert f"workflow: {owner}/graded@v1" in task_yaml, task_yaml
    form_of = organiser.request.get(f"{made_task}/workflow-form").json()
    assert form_of["problem"] is None, form_of
    plan_file = organiser.request.get(f"{made_task}/files/plans/plan.json")
    assert plan_file.ok, plan_file.text()
    plan = json.loads(plan_file.json()["content"])
    assert [step["id"] for step in plan["steps"]] == ["compile", "run", "diff-check"]
    for step in plan["steps"][1:]:
        assert [item["test"] for item in step["batch"]] == plan["tests"]
    run = plan["steps"][1]
    assert run["batch"][0]["inputs"]["args"] == {"value": "--seed 7"}

    # A task of a program of several files, on unicon/classic-folder@v1.
    organiser.goto(contest_page)
    create(
        organiser, "multi", "Create task", "New task", kind="A program of several files"
    )
    multi_task = f"{APP}/api/v1/orgs/{org}/contests/spring/tasks/multi"
    _until(
        lambda: organiser.request.get(f"{multi_task}/workflow-form").json()["workflow"],
        lambda workflow: workflow == "unicon/classic-folder@v1",
        "the task's workflow",
    )
    multi_page = organiser.url
    organiser.reload()
    submitted = organiser.get_by_role("region", name="What entrants submit")
    submitted.get_by_role("group", name="Program").get_by_role(
        "textbox", name="Label shown to entrants"
    ).fill("Your program")
    submitted.get_by_role("group", name="Entry point").get_by_role(
        "textbox", name="Filled in to start with"
    ).fill("main.py")
    submission_rate(organiser, 10, 60)
    save_task(organiser)
    for task_page in (made_page, multi_page):
        write_statement(organiser, task_page, STATEMENT)

    # The contest runs now, and the entrant is in.
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    contest_settings(
        organiser,
        org,
        "spring",
        Name=f"Workflows {stamp}",
        Start=local(now - timedelta(hours=1)),
        End=local(now + timedelta(hours=3)),
    )
    open_contest(organiser, org, "spring")
    register(entrant, org, "spring")
    approve(organiser, org, "spring", entrant_name)
    expect(entrant.get_by_text("You are in")).to_be_visible(timeout=CREATE_TIMEOUT_MS)

    # A solution to each grades accepted.
    entrant.goto(f"{APP}/contests/{org}/spring/tasks/made")
    submit(entrant, 1, SAMPLE)
    assert verdict_of(entrant, 1) == "ACCEPTED"

    program = tmp_path / "program"
    program.mkdir()
    (program / "main.py").write_text(
        "from adder import add\n\na, b = map(int, input().split())\nprint(add(a, b))\n"
    )
    (program / "adder.py").write_text("def add(a, b):\n    return a + b\n")
    entrant.goto(f"{APP}/contests/{org}/spring/tasks/multi")
    form = entrant.get_by_role("form", name="Submit")
    form.get_by_label("Your program: a folder", exact=True).set_input_files(program)
    expect(form.get_by_label(re.compile("^entry|^Entry"))).to_have_value("main.py")
    form.get_by_role("button", name="Submit", exact=True).click()
    expect(entrant.get_by_text("Submitted as #1.")).to_be_visible()
    assert verdict_of(entrant, 1) == "ACCEPTED"
