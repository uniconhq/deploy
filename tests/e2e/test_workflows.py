"""Build a workflow in the editor, version it, share it, and grade with it
and with unicon/classic-folder@v1, on the running compose stack, from the
browser.

An author builds a private workflow on the editor's graph, starting from
the classic one it is made as: an input and a test field declared in place,
the check step taken out and a diff-check added back from the palette, its
ports wired by dragging, one drag refused at the port with the reason, the
run's arguments given text with the new input written in, and a report
entry made from an output. The draft saves with a port left unwired and its
version is refused; wired, saved and versioned, the version keeps the file's
comment. The author shares it with an organiser, who names it in a task:
that task's save writes one `plans/plan.json` in which each per-test step
over a primitive that takes a batch is a single step. A second task names
unicon/classic-folder@v1. A contestant's solution to each grades accepted;
the forge takes a run's `result.json` only when it validates against the
runner's `result.schema.json`, and grades anything else a system error, so
an accepted verdict is a result that validated. Every seeded workflow draws
with no wire behind a box, the same after a reload.

It runs against a stack that is already up with both profiles, `app` and
`agent`, and is skipped unless `UNICON_E2E_URL` names the app. Every name it
makes carries a random suffix, so it runs again on the same stack.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Locator, Page, ViewportSize, expect

from tests.e2e.stack import (
    APP,
    CREATE_TIMEOUT_MS,
    SAMPLE,
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

PREVIEW = "workflow.yaml as a save writes it"
LIMIT_REFUSED = (
    "This port raises a limit, so the task must give it, not the contestant."
)

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


def _drag(page: Page, source: Locator, target: Locator) -> None:
    """A wire dragged from one handle to another, the way a mouse draws it."""
    source.hover()
    page.mouse.down()
    box = target.bounding_box()
    assert box is not None
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


# The preview is CodeMirror, which draws only the lines in view; the whole
# text is its view's document.
PREVIEW_TEXT = """(label) => {
  const host = [...document.querySelectorAll('label')].find(
    (each) => each.textContent.trim() === label);
  const content = document.querySelector(`[aria-labelledby="${host.id}"]`);
  const view = content.cmView && content.cmView.view;
  return view ? view.state.doc.toString() : content.innerText;
}"""


def _preview(page: Page) -> str:
    text: str = page.evaluate(PREVIEW_TEXT, PREVIEW)
    return text


def _preview_shows(page: Page, pattern: str) -> None:
    """Wait until the preview holds `pattern`, a regular expression."""
    page.wait_for_function(
        f"([label, pattern]) => new RegExp(pattern).test(({PREVIEW_TEXT})(label))",
        arg=[PREVIEW, pattern],
    )


def test_a_workflow_is_built_versioned_shared_and_graded_with(
    browser: Browser, tmp_path: Path
) -> None:
    stamp = secrets.token_hex(3)
    author_name, organiser_name = f"e2e-wa-{stamp}", f"e2e-wo-{stamp}"
    contestant_name = f"e2e-wc-{stamp}"
    passwords = {
        name: create_account(name)
        for name in (author_name, organiser_name, contestant_name)
    }
    viewport: ViewportSize = {"width": 1600, "height": 1000}
    author, organiser, contestant = (
        browser.new_context(viewport=viewport).new_page() for _ in range(3)
    )
    for page in (author, organiser, contestant):
        page.set_default_timeout(30_000)
    sign_in(author, author_name, passwords[author_name])
    sign_in(organiser, organiser_name, passwords[organiser_name])
    sign_in(contestant, contestant_name, passwords[contestant_name])

    # Every seeded workflow draws with no wire behind a box, and the same
    # after a reload.
    for seeded in (
        "classic?version=v1",
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

    # The author makes a private workflow, which starts as the classic one.
    owner = author_name.lower()
    author.goto(f"{APP}/workflows")
    made = author.get_by_role("form", name="New workflow")
    made.get_by_role("textbox", name=re.compile("^Name")).fill("graded")
    made.get_by_role("button", name="Make it").click()
    expect(
        author.get_by_role("heading", name=f"{owner}/graded", level=1)
    ).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    expect(author.get_by_role("group", name="Step check")).to_be_visible()
    comment = f"# {owner}/graded, a workflow."
    assert _preview(author).startswith(comment)

    # An input and a test field, declared in place.
    author.get_by_role("button", name="Inputs", exact=True).click()
    adding = author.get_by_role("form", name="Add an input")
    adding.get_by_role("textbox", name="New input").fill("seed")
    adding.get_by_role("combobox", name="Of type").select_option("number")
    adding.get_by_role("button", name="Add an input").click()
    _preview_shows(author, r"\n  seed: number\n")
    author.get_by_role("button", name="Test fields", exact=True).click()
    adding = author.get_by_role("form", name="Add a test field")
    adding.get_by_role("textbox", name="New test field").fill("expected")
    adding.get_by_role("combobox", name="Of type").select_option("file")
    adding.get_by_role("button", name="Add a test field").click()
    _preview_shows(author, r"\n  expected: file\n")

    # The check step out, and a diff-check added back from the palette.
    author.get_by_role("group", name="Step check").get_by_role(
        "button", name=re.compile("^check")
    ).click()
    author.get_by_role("button", name="Remove the step check").click()
    expect(author.get_by_role("group", name="Step check")).to_have_count(0)
    author.get_by_role("button", name="Primitives", exact=True).click()
    author.get_by_role("button", name="Add unicon/diff-check@v2, run per test").click()
    expect(author.get_by_role("group", name="Step diff-check")).to_be_visible()

    # Its output wired by a drag, and a drag a version would refuse, refused.
    _drag(
        author,
        _handle(author, "step:1", "out:output"),
        _handle(author, "step:2", "in:actual"),
    )
    _preview_shows(author, r"actual: \$\{\{ steps\.run\.output \}\}")
    _drag(
        author,
        _handle(author, _node_of(author, "out:submission"), "out:submission"),
        _handle(author, "step:1", "in:time_limit"),
    )
    expect(author.get_by_role("alert").filter(has_text=LIMIT_REFUSED)).to_be_visible()
    assert "time_limit: ${{ inputs.time_limit }}" in _preview(author)

    # Text with the new input written in, and an output reported.
    author.get_by_role("group", name="Step run").get_by_role(
        "button", name=re.compile("^run")
    ).click()
    panel = author.get_by_role("region", name="The step run")
    args = panel.get_by_role("textbox", name=re.compile("^args"))
    args.fill("--seed ")
    args.press("Enter")
    panel.get_by_role("button", name="Write a value into args").click()
    author.get_by_role("menuitem", name="inputs.seed").click()
    _preview_shows(author, r'args: "--seed \$\{\{ inputs\.seed \}\}"')
    author.get_by_role("button", name="The output run.time_ms").click()
    author.get_by_role("menuitem", name="Report it").click()
    _preview_shows(author, r"time_ms_2: \$\{\{ steps\.run\.time_ms \}\}")

    # The draft saves with diff-check's expected unwired; its version does not.
    expect(
        author.get_by_role(
            "note", name="unicon/diff-check@v2 needs the input expected."
        )
    ).to_be_visible()
    author.get_by_role("button", name="Save", exact=True).click()
    expect(author.get_by_text("Saved.", exact=True)).to_be_visible()
    author.get_by_role("button", name="Make the version").click()
    refused = author.get_by_role("alert").filter(has_text="No version was made")
    expect(refused).to_contain_text("steps[2].with")

    # Wired, saved and versioned; the version keeps the file's comment.
    _drag(
        author,
        _handle(author, _node_of(author, "out:expected"), "out:expected"),
        _handle(author, "step:2", "in:expected"),
    )
    _preview_shows(author, r"expected: \$\{\{ test\.expected \}\}")
    author.get_by_role("button", name="Save", exact=True).click()
    expect(author.get_by_text("Saved.", exact=True)).to_be_visible()
    author.get_by_role("button", name="Make the version").click()
    expect(author.get_by_text(f"{owner}/graded@v1 is made.")).to_be_visible()
    version = author.request.get(f"{APP}/api/v1/workflows/{owner}/graded/versions/v1")
    assert version.ok, version.text()
    content = version.json()["content"]
    assert content.startswith(comment)
    assert "--seed ${{ inputs.seed }}" in content

    # Shared with the organiser.
    sharing = author.get_by_role("region", name="Who reads it")
    sharing.get_by_role("radio", name="Shared with the people listed").check()
    sharing.get_by_role("button", name="Make it shared").click()
    expect(sharing.get_by_text("Shared with nobody yet")).to_be_visible()
    sharing.get_by_role("textbox", name="Username").fill(organiser_name)
    sharing.get_by_role("button", name="Share", exact=True).click()
    expect(sharing.get_by_role("list", name="Shared with")).to_contain_text(
        organiser_name.lower()
    )

    # The organiser names it in a task, whose save writes one plan.
    org = f"e2e-wf-{stamp}"
    organiser.goto(f"{APP}/orgs/new")
    create(organiser, org, "Create org")
    create(organiser, "spring", "Create contest", "New contest")
    contest_page = organiser.url
    create(organiser, "made", "Create task", "New task")
    upload_into_task(organiser, "tests/main/1/expected", b"3\n")
    expect(
        organiser.get_by_text(re.compile("Published as publication|Saved as a draft"))
    ).to_be_visible()
    settings = open_settings(organiser, "Task settings")
    settings.get_by_label("Workflow").fill(f"{owner}/graded@v1")
    settings.get_by_label("Rate: count").fill("10")
    settings.get_by_label("Rate: per seconds").fill("60")
    settings.get_by_role("button", name="Save settings").click()
    made_task = f"{APP}/api/v1/orgs/{org}/contests/spring/tasks/made"
    named = f"workflow: {owner}/graded@v1"
    task_yaml = ""
    for _ in range(30):
        task_yaml = organiser.request.get(f"{made_task}/files/task.yaml").json()[
            "content"
        ]
        if named in task_yaml:
            break
        organiser.wait_for_timeout(1000)
    assert named in task_yaml, task_yaml
    form_of = organiser.request.get(f"{made_task}/workflow-form").json()
    assert form_of["problem"] is None, form_of
    organiser.reload()
    settings = open_settings(organiser, "Task settings")
    settings.get_by_role("button", name="Give seed a value").click()
    settings.get_by_label("seed value").fill("7")
    settings.get_by_role("button", name="Save settings").click()
    expect(
        organiser.get_by_text(re.compile(r"Published as publication \d+\."))
    ).to_be_visible(timeout=CREATE_TIMEOUT_MS)
    tasks = f"{APP}/api/v1/orgs/{org}/contests/spring/tasks"
    plan_file = organiser.request.get(f"{tasks}/made/files/plans/plan.json")
    assert plan_file.ok, plan_file.text()
    plan = json.loads(plan_file.json()["content"])
    assert [step["id"] for step in plan["steps"]] == ["compile", "run", "diff-check"]
    for step in plan["steps"][1:]:
        assert [item["test"] for item in step["batch"]] == plan["tests"]
    run = plan["steps"][1]
    assert run["batch"][0]["inputs"]["args"] == {"value": "--seed 7"}

    # A task on unicon/classic-folder@v1.
    organiser.goto(contest_page)
    create(organiser, "multi", "Create task", "New task")
    settings = open_settings(organiser, "Task settings")
    settings.get_by_label("Workflow").fill("unicon/classic-folder@v1")
    settings.get_by_label("Rate: count").fill("10")
    settings.get_by_label("Rate: per seconds").fill("60")
    settings.get_by_role("button", name="Save settings").click()
    multi_task = f"{APP}/api/v1/orgs/{org}/contests/spring/tasks/multi"
    for _ in range(30):
        form_of = organiser.request.get(f"{multi_task}/workflow-form").json()
        if form_of["workflow"] == "unicon/classic-folder@v1":
            break
        organiser.wait_for_timeout(1000)
    assert form_of["problem"] is None, form_of
    organiser.reload()
    settings = open_settings(organiser, "Task settings")
    settings.get_by_role("button", name="Remove submission").click()
    settings.get_by_role("button", name="Give program form details").click()
    settings.get_by_label("program label").fill("Your program")
    settings.get_by_role("button", name="Give entry form details").click()
    settings.get_by_label("entry default").fill("main.py")
    settings.get_by_role("button", name="Save settings").click()
    expect(
        organiser.get_by_text(re.compile(r"Published as publication \d+\."))
    ).to_be_visible(timeout=CREATE_TIMEOUT_MS)

    # The contest runs now, and the contestant is in.
    organiser.goto(contest_page)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    settings = open_settings(organiser, "Contest settings")
    settings.get_by_label("Name", exact=True).fill(f"Workflows {stamp}")
    settings.get_by_label("State", exact=True).select_option("published")
    settings.get_by_label("Start", exact=True).fill(local(now - timedelta(hours=1)))
    settings.get_by_label("End", exact=True).fill(local(now + timedelta(hours=3)))
    settings.get_by_role("button", name="Save settings").click()
    expect(organiser.get_by_text(re.compile(r"Saved as version"))).to_be_visible()
    contestant.goto(f"{APP}/contests/{org}/spring")
    contestant.get_by_role("button", name="Register").click()
    expect(contestant.get_by_text("Your registration is waiting")).to_be_visible()
    organiser.goto(f"{APP}/orgs/{org}/contests/spring/contestants")
    organiser.get_by_role("button", name=re.compile("^Approve")).click()
    expect(organiser.get_by_text("Approved")).to_be_visible()
    expect(contestant.get_by_text("You are in")).to_be_visible(
        timeout=CREATE_TIMEOUT_MS
    )

    # A solution to each grades accepted.
    contestant.goto(f"{APP}/contests/{org}/spring/tasks/made")
    submit(contestant, 1, SAMPLE)
    assert verdict_of(contestant, 1) == "ACCEPTED"

    program = tmp_path / "program"
    program.mkdir()
    (program / "main.py").write_text(
        "from adder import add\n\na, b = map(int, input().split())\nprint(add(a, b))\n"
    )
    (program / "adder.py").write_text("def add(a, b):\n    return a + b\n")
    contestant.goto(f"{APP}/contests/{org}/spring/tasks/multi")
    form = contestant.get_by_role("form", name="Submit")
    form.get_by_label("Your program: a folder", exact=True).set_input_files(program)
    expect(form.get_by_label(re.compile("^entry|^Entry"))).to_have_value("main.py")
    form.get_by_role("button", name="Submit", exact=True).click()
    expect(contestant.get_by_text("Submitted as #1.")).to_be_visible()
    assert verdict_of(contestant, 1) == "ACCEPTED"
