"""Seeding unicon/classic@v1: what is made on a fresh forge, what is left
alone on a rerun, and the shape the forge package looks for.

The forge package finds a workflow as a repository `<owner>/<name>.workflow`
carrying the topic `unicon-workflow`, with a git tag per version, so these
tests hold the names the seeding has to produce. The fake here is a Compose
whose exec reads the curl configuration bootstrap wrote and answers as a
Forgejo holding the given organisation, repository, commit, topics and tags
would, keeping every request.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from bootstrap.compose import Compose
from bootstrap.forgejo import API_INSIDE_THE_CONTAINER, Forgejo, ForgejoError
from bootstrap.workflows import (
    CLASSIC,
    CLASSIC_REPO,
    CLASSIC_VERSION,
    DEFAULT_BRANCH,
    PLATFORM_ORG,
    SEED_FILES,
    WORKFLOW_TOPIC,
    Workflows,
    seed_files,
)

DEPLOY = Path(__file__).resolve().parent.parent
REPO = f"/repos/{PLATFORM_ORG}/{CLASSIC_REPO}"
TOKEN = "provisioning-token"
FILES = {"workflow.yaml": b"name: unicon/classic\n", "README.md": b"# classic\n"}


def _read_config(stdin: str) -> dict[str, list[str]]:
    """The curl configuration bootstrap wrote, as name to values."""
    fields: dict[str, list[str]] = {}
    for line in stdin.splitlines():
        name, _, value = line.partition(" = ")
        fields.setdefault(name, []).append(json.loads(value) if value else "")
    return fields


class FakeForge(Compose):
    """A Forgejo in the state given, answering the calls the seeding makes
    and changing state the way Forgejo would.
    """

    def __init__(
        self,
        *,
        org: bool = False,
        repo: bool = False,
        head: str | None = None,
        topics: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
    ) -> None:
        super().__init__(Path("."), [])
        self.org = org
        self.repo = repo
        self.head = head
        self.topics = list(topics)
        self.tags: dict[str, str] = dict.fromkeys(tags, "older")
        self.requests: list[tuple[str, str]] = []
        self.bodies: dict[tuple[str, str], Any] = {}
        self.configs: list[dict[str, list[str]]] = []

    def execute(
        self,
        service: str,
        *arguments: str,
        user: str | None = None,
        stdin: str | None = None,
    ) -> str:
        assert arguments == ("curl", "--config", "-")
        assert stdin is not None
        config = _read_config(stdin)
        self.configs.append(config)
        method = config["request"][0]
        path = config["url"][0].removeprefix(API_INSIDE_THE_CONTAINER)
        body = json.loads(config["data"][0]) if "data" in config else None
        self.requests.append((method, path))
        self.bodies[(method, path)] = body
        status, answer = self._answer(method, path, body)
        return f"{json.dumps(answer) if answer is not None else ''}\n{status}"

    def _answer(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        absent = (404, {"message": "not found"})
        if (method, path) == ("GET", f"/orgs/{PLATFORM_ORG}"):
            return (200, {"username": PLATFORM_ORG}) if self.org else absent
        if (method, path) == ("POST", "/orgs"):
            self.org = True
            return 201, {"username": body["username"]}
        if (method, path) == ("GET", REPO):
            return (200, {"name": CLASSIC_REPO}) if self.repo else absent
        if (method, path) == ("POST", f"/orgs/{PLATFORM_ORG}/repos"):
            self.repo = True
            return 201, {"name": body["name"]}
        if (method, path) == ("GET", f"{REPO}/branches/{DEFAULT_BRANCH}"):
            if self.head is None:
                return absent
            return 200, {"name": DEFAULT_BRANCH, "commit": {"id": self.head}}
        if (method, path) == ("POST", f"{REPO}/contents"):
            self.head = "c0ffee"
            return 201, {"commit": {"sha": self.head}}
        if (method, path) == ("GET", f"{REPO}/topics"):
            return 200, {"topics": list(self.topics)}
        if method == "PUT" and path.startswith(f"{REPO}/topics/"):
            self.topics.append(path.rpartition("/")[2])
            return 204, None
        if method == "GET" and path.startswith(f"{REPO}/tags/"):
            tag = path.rpartition("/")[2]
            return (200, {"name": tag}) if tag in self.tags else absent
        if (method, path) == ("POST", f"{REPO}/tags"):
            self.tags[body["tag_name"]] = body["target"]
            return 201, {"name": body["tag_name"]}
        raise AssertionError(f"unexpected {method} {path}")


def _seed(forge: FakeForge) -> list[bool]:
    """Every piece in the order main.py runs them; returns what each made."""
    workflows = Workflows(Forgejo(forge, "forgejo"), TOKEN)
    made = [
        workflows.ensure_platform_org(),
        workflows.ensure_repository(PLATFORM_ORG, CLASSIC_REPO),
    ]
    head, created = workflows.ensure_first_commit(
        PLATFORM_ORG, CLASSIC_REPO, FILES, message="Seed"
    )
    made.append(created)
    made.append(workflows.ensure_mark(PLATFORM_ORG, CLASSIC_REPO, WORKFLOW_TOPIC))
    made.append(
        workflows.ensure_version(PLATFORM_ORG, CLASSIC_REPO, CLASSIC_VERSION, head)
    )
    return made


def test_a_fresh_forge_gets_every_piece() -> None:
    forge = FakeForge()

    assert _seed(forge) == [True, True, True, True, True]
    assert [request for request in forge.requests if request[0] != "GET"] == [
        ("POST", "/orgs"),
        ("POST", f"/orgs/{PLATFORM_ORG}/repos"),
        ("POST", f"{REPO}/contents"),
        ("PUT", f"{REPO}/topics/{WORKFLOW_TOPIC}"),
        ("POST", f"{REPO}/tags"),
    ]


def test_a_seeded_forge_is_read_and_not_written() -> None:
    forge = FakeForge(
        org=True,
        repo=True,
        head="abc123",
        topics=(WORKFLOW_TOPIC,),
        tags=(CLASSIC_VERSION,),
    )

    assert _seed(forge) == [False, False, False, False, False]
    assert {method for method, _ in forge.requests} == {"GET"}


def test_the_platform_org_is_limited_and_made_by_the_platform_account() -> None:
    forge = FakeForge()

    _seed(forge)

    assert forge.bodies[("POST", "/orgs")] == {
        "username": PLATFORM_ORG,
        "description": "Unicon's own workflows and primitives",
        "visibility": "limited",
        "repo_admin_change_team_access": False,
    }


def test_the_repository_is_public_on_main_with_no_commit_of_forgejos_own() -> None:
    forge = FakeForge(org=True)

    _seed(forge)

    assert forge.bodies[("POST", f"/orgs/{PLATFORM_ORG}/repos")] == {
        "name": f"{CLASSIC}.workflow",
        "private": False,
        "default_branch": "main",
        "auto_init": False,
    }


def test_the_files_land_in_one_commit_that_the_version_points_at() -> None:
    forge = FakeForge(org=True, repo=True)

    _seed(forge)

    commit = forge.bodies[("POST", f"{REPO}/contents")]
    assert commit["branch"] == "main"
    assert commit["new_branch"] == "main"
    assert commit["message"] == "Seed"
    assert {
        entry["path"]: base64.b64decode(entry["content"]) for entry in commit["files"]
    } == FILES
    assert all(entry["operation"] == "create" for entry in commit["files"])
    assert forge.bodies[("POST", f"{REPO}/tags")] == {
        "tag_name": CLASSIC_VERSION,
        "target": "c0ffee",
    }


def test_a_repository_with_a_commit_keeps_it_and_is_tagged_at_its_head() -> None:
    forge = FakeForge(org=True, repo=True, head="abc123", topics=(WORKFLOW_TOPIC,))

    assert _seed(forge) == [False, False, False, False, True]
    assert ("POST", f"{REPO}/contents") not in forge.requests
    assert forge.tags == {CLASSIC_VERSION: "abc123"}


def test_an_existing_version_is_not_moved() -> None:
    forge = FakeForge(org=True, repo=True, head="abc123", tags=(CLASSIC_VERSION,))

    _seed(forge)

    assert forge.tags == {CLASSIC_VERSION: "older"}


def test_the_mark_is_added_beside_topics_already_there() -> None:
    forge = FakeForge(org=True, repo=True, head="abc123", topics=("other",))

    _seed(forge)

    assert forge.topics == ["other", WORKFLOW_TOPIC]


def test_every_call_carries_the_token_and_no_password() -> None:
    forge = FakeForge()

    _seed(forge)

    for config in forge.configs:
        assert f"Authorization: token {TOKEN}" in config["header"]
        assert "user" not in config


def test_a_refusal_other_than_absent_stops_the_run() -> None:
    class Refusing(FakeForge):
        def _answer(self, method: str, path: str, body: Any) -> tuple[int, Any]:
            return 403, {"message": "no"}

    with pytest.raises(ForgejoError, match="403"):
        _seed(Refusing())


def test_the_seeded_definition_is_the_classic_workflow() -> None:
    files = seed_files(DEPLOY, CLASSIC)

    assert set(files) == set(SEED_FILES) == {"workflow.yaml", "README.md"}
    definition = files["workflow.yaml"].decode().splitlines()
    assert "name: unicon/classic" in definition
    assert f"version: {CLASSIC_VERSION}" in definition
    assert [line.split("use: ")[1] for line in definition if "use: " in line] == [
        "unicon/compile@v1",
        "unicon/sandbox-run@v1",
        "unicon/diff-check@v1",
    ]
    assert files["README.md"].startswith(b"# classic\n")
