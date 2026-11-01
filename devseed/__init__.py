"""What the development stack is seeded with, made through the API as each
person would make it, every submission graded by the real grading machine.

Five people:

- alice, admin of `riverside`, the Riverside Coding Club, who runs its
  contests and writes `riverside/standard` and `riverside/coins`;
- dan, a manager of `riverside` and admin of `judgekit`, an org that
  publishes judging workflows anyone may build on, who also keeps
  `dan/round-trip` of his own, shared with alice alone;
- bob, carol and erin, entrants: each alone in Spring Open 2026, and in
  Summer Relay 2026 bob and carol as the team "Bob & Carol" and erin alone.

The workflows use each other's versions as steps:

- `judgekit/check@v1`, a checker the task brings, compiled and run by
  `unicon/exec@v3`;
- `judgekit/special@v1`, a run judged by a checker: the solution run by
  `unicon/exec@v3`, and `judgekit/check@v1` as a box;
- `riverside/standard@v1`, private to the club: `unicon/classic@v5`
  under the club's limits;
- `riverside/coins@v1`, private to the club: the solution run by
  `unicon/exec@v3`, and `judgekit/check@v1` as a box, with a checker of
  its own given a 2 MB table, `data/fewest.bin`, uploaded into the draft,
  as its answer; its draft is ahead of v1 by a changed checker;
- `dan/round-trip@v1`: `unicon/classic@v5` twice, once each way;
- `bob/practice@v1`: `unicon/classic@v5` under bob's own limits, used by
  no task.

Three contests, all riverside's:

- Spring Open 2026, which started three hours before the seed and is
  closed by it, with results released: Sum of Two on unicon/classic@v5,
  Grid Islands on riverside/standard@v1 with two subtasks and its points
  on release,
  Making Change on riverside/coins@v1, due once bob's submission is in,
  so carol's and erin's a minute later are late, and Guess the Number on
  unicon/interactive@v3. Two
  leaderboards: ICPC standings and points. A question bob asked, answered
  in public, and an announcement.
- Summer Relay 2026, running: Closest Pair on judgekit/special@v1, whose
  checker gives part of a test's points to a pair that is close but not
  closest, and Round Trip on dan/round-trip@v1, a relay points board,
  two more days on Round Trip for erin, and a question from her not yet
  answered.
- Autumn Open 2026, a draft with one task not written yet.

The seed's files are beside this module: `workflows/<owner>/<name>/v<n>/`
for each version and `draft/` for what a draft changes after its last
version; `contests/<contest>/tasks/<task>/` for each task's files; and
`programs/<contest>/<task>/<person>-<n>.<ext>` for each submission, sent
in that order.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from devseed import contests
from tests.e2e import api

HERE = Path(__file__).resolve().parent
PEOPLE = ("alice", "bob", "carol", "dan", "erin")
CLUB = "riverside"
KIT = "judgekit"
WORKFLOW_FILE = "workflow.yaml"
LANGUAGES = {".py": "python", ".cpp": "cpp", ".java": "java"}
ON_TIME = "bob"
"""Who sends Spring's late task its first submission, before it is due."""
LATE_BY = timedelta(minutes=1)
"""How long after the due time everyone else's submissions to it are sent."""


@dataclass(frozen=True)
class Workflow:
    """A workflow made by `maker` and owned by `owner`, its versions
    published in order, what its draft changes after them, who reads it,
    and any file `made` adds to the draft by an upload before v1."""

    maker: str
    owner: str
    name: str
    versions: tuple[str, ...] = ("v1",)
    visibility: str = "private"
    readers: tuple[str, ...] = ()
    made: dict[str, Callable[[], bytes]] = field(default_factory=dict)


def fewest_coins() -> bytes:
    """The fewest of coins 1, 7, 10 and 25 for each amount to 1,000,000,
    two bytes each, little-endian, as riverside/coins' checker reads them."""
    fewest = [0] * 1_000_001
    for amount in range(1, len(fewest)):
        fewest[amount] = 1 + min(
            fewest[amount - coin] for coin in (1, 7, 10, 25) if coin <= amount
        )
    return b"".join(count.to_bytes(2, "little") for count in fewest)


WORKFLOWS = (
    Workflow("dan", KIT, "check", visibility="public"),
    Workflow("dan", KIT, "special", visibility="public"),
    Workflow("alice", CLUB, "standard"),
    Workflow("alice", CLUB, "coins", made={"data/fewest.bin": fewest_coins}),
    Workflow("dan", "dan", "round-trip", visibility="shared", readers=("alice",)),
    Workflow("bob", "bob", "practice"),
)
"""In the order they must be published: a box's version before the
workflow that uses it."""


def seed(callers: dict[str, api.Caller], say: Callable[[str], None]) -> None:
    """Everything above, each step as the person who would take it."""
    alice, dan = callers["alice"], callers["dan"]
    say("making the orgs")
    make_orgs(alice, dan)
    say("publishing the workflows")
    for workflow in WORKFLOWS:
        publish(callers[workflow.maker], workflow)
    hold_contests(callers, say)


def hold_contests(callers: dict[str, api.Caller], say: Callable[[str], None]) -> None:
    """The three contests, Spring Open run and closed, once the workflows
    their tasks use are published."""
    alice = callers["alice"]
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    spring_start, relay_start = now - timedelta(hours=3), now - timedelta(hours=1)
    spring_end, relay_end = now + timedelta(hours=2), now + timedelta(days=14)
    say("writing the contests and their tasks")
    write(alice, contests.SPRING, contests.spring("draft", spring_start, spring_end))
    write(alice, contests.RELAY, contests.relay("draft", relay_start, relay_end))
    write(alice, contests.AUTUMN, contests.autumn(now + timedelta(days=60)))
    for contest, tasks in (
        (contests.SPRING, contests.SPRING_TASKS),
        (contests.RELAY, contests.RELAY_TASKS),
    ):
        for task in tasks:
            api.save_task(alice, task_path(contest, task), task_files(contest, task))
    say("opening Spring Open and Summer Relay once their tasks are published")
    api.open_once_published(
        alice,
        contest_path(contests.SPRING),
        contests.spring("published", spring_start, spring_end),
    )
    api.open_once_published(
        alice,
        contest_path(contests.RELAY),
        contests.relay("published", relay_start, relay_end),
    )
    say("letting the entrants in")
    spring = contest_path(contests.SPRING)
    for who in ("bob", "carol", "erin"):
        register(callers[who], spring)
    relay = contest_path(contests.RELAY)
    register(callers["bob"], relay, "Bob & Carol")
    callers["bob"].send("POST", f"{relay}/entrants/mine/invites", {"username": "carol"})
    for invite in callers["carol"].get("/me/invites"):
        callers["carol"].send("POST", f"/me/invites/{invite['id']}/accept", {})
    erin = register(callers["erin"], relay, "Erin")
    for contest in (spring, relay):
        approve_everyone(alice, contest)
    alice.send(
        "PUT",
        f"{relay}/organise/entrants/{erin}/extension",
        {"seconds": 2 * 86400, "tasks": ["round-trip"]},
    )
    say("asking and answering")
    threads(callers)
    say("submitting")
    programs = submissions()
    on_time = [p for p in programs if p[2] == contests.LATE_TASK and p[0] == ON_TIME]
    late = [p for p in programs if p[2] == contests.LATE_TASK and p[0] != ON_TIME]
    sent = [submit(callers[p[0]], *p[1:]) for p in on_time]
    due = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
    time.sleep(1)
    api.write_contest(
        alice, spring, contests.spring("published", spring_start, spring_end, due)
    )
    sent += [
        submit(callers[p[0]], *p[1:]) for p in programs if p[2] != contests.LATE_TASK
    ]
    say(f"waiting until {contests.LATE_TASK} is a minute past due")
    time.sleep(max(0.0, (due + LATE_BY - datetime.now(UTC)).total_seconds()))
    sent += [submit(callers[p[0]], *p[1:]) for p in late]
    say(f"waiting for {len(sent)} gradings")
    for who, task, number in sent:
        grading = api.graded(callers[who], task, number)
        say(f"  {who} {task.rsplit('/', 1)[1]} #{number}: {grading['status']}")
    say("closing Spring Open and releasing its results")
    closed = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=1)
    time.sleep(1)
    api.write_contest(
        alice, spring, contests.spring("published", spring_start, closed, due)
    )
    for task in contests.SPRING_TASKS:
        alice.send(
            "POST",
            f"{task_path(contests.SPRING, task)}/results/release",
            {"confirm": True},
        )


def make_orgs(alice: api.Caller, dan: api.Caller) -> None:
    alice.send("POST", "/orgs", {"name": CLUB})
    alice.send(
        "PATCH",
        f"/orgs/{CLUB}",
        {
            "display_name": "Riverside Coding Club",
            "description": "Weekly practice and two opens a year.",
        },
    )
    dan.send("POST", "/orgs", {"name": KIT})
    dan.send(
        "PATCH",
        f"/orgs/{KIT}",
        {
            "display_name": "Judge Kit",
            "description": "Judging workflows to build on: checkers and judges.",
        },
    )
    alice.send("POST", f"/orgs/{CLUB}/roles", {"username": "dan", "role": "manager"})


def publish(maker: api.Caller, workflow: Workflow) -> None:
    """Make the workflow, publish each version from its folder, save what
    its draft changes after them, and let its readers in."""
    path = f"/workflows/{workflow.owner}/{workflow.name}"
    folder = HERE / "workflows" / workflow.owner / workflow.name
    maker.send("POST", "/workflows", {"owner": workflow.owner, "name": workflow.name})
    uploads = {
        name: upload(maker, path, name, make()) for name, make in workflow.made.items()
    }
    for number, version in enumerate(workflow.versions):
        save_draft(maker, path, folder / version, uploads if number == 0 else {})
        maker.send("POST", f"{path}/versions", {"version": version})
    if (folder / "draft").is_dir():
        save_draft(maker, path, folder / "draft", {})
    if workflow.visibility != "private":
        maker.send("PUT", f"{path}/visibility", {"visibility": workflow.visibility})
    for reader in workflow.readers:
        maker.send("PUT", f"{path}/readers/{reader}", {})


def save_draft(
    maker: api.Caller, path: str, folder: Path, uploads: dict[str, str]
) -> None:
    """Save the folder's files over the draft, `workflow.yaml` as its
    definition, and each of `uploads` by its upload."""
    draft = maker.get(path)["draft"]
    changes: list[dict[str, Any]] = [
        {
            "path": name,
            "token": api.token_of(maker, f"{path}/files/{name}"),
            "encoding": "utf-8",
            "content": file.read_text(encoding="utf-8"),
        }
        for name, file in files_of(folder).items()
        if name != WORKFLOW_FILE
    ]
    changes += [
        {
            "path": name,
            "token": api.token_of(maker, f"{path}/files/{name}"),
            "upload": id_,
        }
        for name, id_ in uploads.items()
    ]
    definition = folder / WORKFLOW_FILE
    content = (
        definition.read_text(encoding="utf-8")
        if definition.exists()
        else draft["content"]
    )
    maker.send(
        "PUT",
        f"{path}/draft",
        {"content": content, "token": draft["token"], "changes": changes},
    )


def upload(maker: api.Caller, path: str, name: str, content: bytes) -> str:
    """Upload `content` for the draft's file `name`; the upload's id."""
    slot = maker.send(
        "POST",
        f"{path}/uploads",
        {
            "path": name,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )
    api.put_upload(maker, slot, content)
    maker.send("POST", f"{path}/uploads/{slot['id']}/complete", {})
    return str(slot["id"])


def write(alice: api.Caller, contest: str, settings: str) -> None:
    """Make the contest and its tasks, and write its settings."""
    alice.send("POST", f"/orgs/{CLUB}/contests", {"name": contest})
    for task in contests_tasks(contest):
        alice.send("POST", f"{contest_path(contest)}/tasks", {"name": task})
    api.write_contest(alice, contest_path(contest), settings)


def contests_tasks(contest: str) -> tuple[str, ...]:
    return {
        contests.SPRING: contests.SPRING_TASKS,
        contests.RELAY: contests.RELAY_TASKS,
        contests.AUTUMN: contests.AUTUMN_TASKS,
    }[contest]


def register(entrant: api.Caller, contest: str, name: str | None = None) -> str:
    """Register the caller, under `name` where entrants are teams; the new
    entrant's id."""
    made = entrant.send("POST", f"{contest}/entrants", {"name": name} if name else {})
    return str(made["id"])


def approve_everyone(organiser: api.Caller, contest: str) -> None:
    for row in api.entrant_rows(organiser, contest):
        if row.get("status") == "pending":
            organiser.send(
                "POST", f"{contest}/organise/entrants/{row['id']}/approve", {}
            )


def threads(callers: dict[str, api.Caller]) -> None:
    """Bob's question on Grid Islands, answered for everyone; an
    announcement; and erin's question on Closest Pair, waiting."""
    spring, relay = contest_path(contests.SPRING), contest_path(contests.RELAY)
    asked = callers["bob"].send(
        "POST",
        f"{spring}/questions",
        {
            "title": "Diagonal land",
            "body": "Are two land cells that touch only at a corner the same island?",
            "task": "islands",
        },
    )
    callers["alice"].send(
        "POST",
        f"{spring}/clarifications/{asked['asker']}/{asked['number']}/announcement",
        {
            "title": "Grid Islands: corners do not join",
            "body": "Cells that touch only at a corner are separate islands.",
        },
    )
    callers["alice"].send(
        "POST",
        f"{spring}/announcements",
        {
            "title": "Results after the close",
            "body": "Grid Islands' points are shown once we release the results.",
        },
    )
    callers["erin"].send(
        "POST",
        f"{relay}/questions",
        {
            "title": "Same number twice?",
            "body": "If k is 10 and 5 appears once, may I print its place twice?",
            "task": "pairs",
        },
    )


def submissions() -> list[tuple[str, str, str, Path]]:
    """Each program under `programs/`, as who sends it to which task: in
    each contest and task, everyone's first before anyone's second."""
    found: list[tuple[str, str, str, Path]] = []
    for contest in sorted((HERE / "programs").iterdir()):
        for task in sorted(contest.iterdir()):
            programs = sorted(task.iterdir(), key=lambda p: (attempt(p), p.name))
            found += [
                (p.stem.rsplit("-", 1)[0], contest.name, task.name, p) for p in programs
            ]
    return found


def attempt(program: Path) -> int:
    return int(program.stem.rsplit("-", 1)[1])


def submit(
    entrant: api.Caller, contest: str, task: str, program: Path
) -> tuple[str, str, int]:
    who = program.stem.rsplit("-", 1)[0]
    path = task_path(contest, task)
    number = api.submit(
        entrant,
        path,
        program.name,
        program.read_bytes(),
        values={"language": LANGUAGES[program.suffix]},
    )
    return who, path, number


def task_files(contest: str, task: str) -> dict[str, str]:
    folder = HERE / "contests" / contest / "tasks" / task
    return {
        name: file.read_text(encoding="utf-8")
        for name, file in files_of(folder).items()
    }


def files_of(folder: Path) -> dict[str, Path]:
    """Every file under `folder`, by its path from there."""
    return {
        file.relative_to(folder).as_posix(): file
        for file in sorted(folder.rglob("*"))
        if file.is_file() and "__pycache__" not in file.parts
    }


def contest_path(contest: str) -> str:
    return f"/orgs/{CLUB}/contests/{contest}"


def task_path(contest: str, task: str) -> str:
    return f"{contest_path(contest)}/tasks/{task}"
