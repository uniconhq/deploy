"""Seeding unicon/classic@v1: what is made on a fresh forge, what is left
alone on a rerun, the shape the forge package looks for, and the one way v1
is ever rewritten.

The forge package finds a workflow as a public repository
`<owner>/<name>.workflow` carrying the topic `unicon-workflow`, with a git tag
per version, so these tests hold the names the seeding has to produce. They
run bootstrap's seeding step against the in-memory Forgejo of forgejo_fake.py.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from bootstrap import main
from bootstrap.forgejo import ForgejoError
from bootstrap.images import Manifest
from bootstrap.summary import Summary
from bootstrap.workflows import CLASSIC, CLASSIC_REPO, SEED_FILES, seed_files
from tests.forgejo_fake import FakeForgejo

DEPLOY = Path(__file__).resolve().parent.parent
ORG = "unicon"
FULL = f"{ORG}/{CLASSIC_REPO}"
REPO = f"/repos/{FULL}"
TOKEN = "provisioning-token"
DIGEST = "@sha256:" + "0" * 64
NO_PRIMITIVES = Manifest(
    path=DEPLOY / "images.json",
    images={
        name: f"ghcr.io/uniconhq/{name}{DIGEST}"
        for name in ("harness", "clone", "socket-filter")
    },
    primitives=(),
)


def _deploy_with(tmp_path: Path, definition: bytes) -> Path:
    """A deploy directory whose classic definition is `definition`."""
    root = tmp_path / "workflows" / CLASSIC
    root.mkdir(parents=True)
    (root / "workflow.yaml").write_bytes(definition)
    (root / "README.md").write_bytes(b"# classic\n")
    return tmp_path


def _seed(
    forge: FakeForgejo, directory: Path = DEPLOY, rewrite: bool = False
) -> Summary:
    summary = Summary()
    main._seed_platform(
        forge,
        directory,
        NO_PRIMITIVES,
        {"UNICON_FORGE_ADMIN_TOKEN": TOKEN},
        summary,
        rewrite,
    )
    return summary


def test_a_fresh_forge_gets_every_piece() -> None:
    forge = FakeForgejo()

    summary = _seed(forge)

    assert summary.lines()[0] == "created: 4, already present: 0"
    assert forge.writes() == [
        ("POST", "/orgs"),
        ("POST", f"/orgs/{ORG}/repos"),
        ("POST", f"{REPO}/contents"),
        ("POST", f"{REPO}/tags"),
        ("PUT", f"{REPO}/topics/unicon-workflow"),
    ]
    repo = forge.repos[FULL]
    assert repo.files_at("v1") == seed_files(DEPLOY, CLASSIC)
    assert repo.topics == ["unicon-workflow"]


def test_a_seeded_forge_is_read_and_not_written() -> None:
    forge = FakeForgejo()
    _seed(forge)
    forge.requests.clear()

    summary = _seed(forge)

    assert summary.lines()[0] == "created: 0, already present: 4"
    assert forge.writes() == []


def test_the_platform_org_is_limited_and_made_by_the_platform_account() -> None:
    forge = FakeForgejo()

    _seed(forge)

    assert forge.body_of("POST", "/orgs") == {
        "username": ORG,
        "description": "Unicon's own workflows and primitives",
        "visibility": "limited",
        "repo_admin_change_team_access": False,
    }


def test_the_repository_is_public_on_main_with_no_commit_of_forgejos_own() -> None:
    forge = FakeForgejo()

    _seed(forge)

    assert forge.body_of("POST", f"/orgs/{ORG}/repos") == {
        "name": f"{CLASSIC}.workflow",
        "private": False,
        "default_branch": "main",
        "auto_init": False,
    }


def test_the_files_land_in_one_commit_that_the_version_points_at() -> None:
    forge = FakeForgejo()

    _seed(forge)

    commit = forge.body_of("POST", f"{REPO}/contents")
    assert commit["branch"] == "main"
    assert commit["new_branch"] == "main"
    assert commit["message"] == f"Seed {ORG}/{CLASSIC}@v1"
    assert {
        entry["path"]: base64.b64decode(entry["content"]) for entry in commit["files"]
    } == seed_files(DEPLOY, CLASSIC)
    assert all(entry["operation"] == "create" for entry in commit["files"])
    repo = forge.repos[FULL]
    assert repo.tags == {"v1": repo.head}


def test_a_commit_holding_the_files_without_a_tag_is_tagged_where_it_is() -> None:
    """An earlier run that stopped between the commit and the tag."""
    forge = FakeForgejo()
    repo = forge.seed(FULL, seed_files(DEPLOY, CLASSIC), topics=("unicon-workflow",))
    head = repo.head

    _seed(forge)

    assert ("POST", f"{REPO}/contents") not in forge.requests
    assert repo.tags == {"v1": head}


def test_a_changed_definition_leaves_v1_as_it_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    forge = FakeForgejo()
    repo = forge.seed(
        FULL, seed_files(DEPLOY, CLASSIC), tags=("v1",), topics=("unicon-workflow",)
    )
    before = dict(repo.tags)

    _seed(forge, _deploy_with(tmp_path, b"name: unicon/classic\nversion: v1\n"))

    assert forge.writes() == []
    assert repo.tags == before
    assert "differs from unicon/classic@v1" in capsys.readouterr().out


def test_rewrite_moves_v1_to_a_commit_of_the_definition_here(tmp_path: Path) -> None:
    forge = FakeForgejo()
    repo = forge.seed(
        FULL, seed_files(DEPLOY, CLASSIC), tags=("v1",), topics=("unicon-workflow",)
    )
    old = repo.tags["v1"]
    directory = _deploy_with(tmp_path, b"name: unicon/classic\nversion: v1\n")

    summary = _seed(forge, directory, rewrite=True)

    assert repo.tags["v1"] != old
    assert repo.files_at("v1") == seed_files(directory, CLASSIC)
    assert old in repo.commits
    assert f"  created  workflow {ORG}/{CLASSIC} version v1 (rewritten)" in (
        summary.lines()
    )
    forge.requests.clear()
    _seed(forge, directory, rewrite=True)
    assert forge.writes() == []


def test_the_mark_is_added_beside_topics_already_there() -> None:
    forge = FakeForgejo()
    repo = forge.seed(FULL, topics=("other",))

    _seed(forge)

    assert repo.topics == ["other", "unicon-workflow"]


def test_every_call_carries_the_token_and_no_password() -> None:
    forge = FakeForgejo()

    _seed(forge)

    for config in forge.configs:
        assert f"Authorization: token {TOKEN}" in config["header"]
        assert "user" not in config


def test_a_refusal_other_than_absent_stops_the_run() -> None:
    forge = FakeForgejo()
    forge.refusal = 403

    with pytest.raises(ForgejoError, match="403"):
        _seed(forge)


def test_rewriting_is_refused_outside_a_development_stack() -> None:
    main.refuse_rewrite_outside_development(rewrite=True, development=True)
    main.refuse_rewrite_outside_development(rewrite=False, development=False)
    with pytest.raises(ValueError, match="development stack"):
        main.refuse_rewrite_outside_development(rewrite=True, development=False)
    assert main.is_development(["compose.yaml", "compose.dev.yaml"])
    assert not main.is_development(["compose.yaml"])


def test_the_seeded_definition_wires_compile_into_run_into_check() -> None:
    files = seed_files(DEPLOY, CLASSIC)

    assert set(files) == set(SEED_FILES) == {"workflow.yaml", "README.md"}
    definition = [line.strip() for line in files["workflow.yaml"].decode().splitlines()]
    assert "name: unicon/classic" in definition
    assert "version: v1" in definition
    assert [line.split("use: ")[1] for line in definition if "use: " in line] == [
        "unicon/compile@v1",
        "unicon/sandbox-run@v1",
        "unicon/diff-check@v1",
    ]
    for wiring in (
        "language: ${{ inputs.submission.language }}",
        "binary: ${{ steps.compile.binary }}",
        "actual: ${{ steps.run.output }}",
        "outcome: ${{ steps.check.outcome }}",
        "time_ms: ${{ steps.run.time_ms }}",
        "memory_kb: ${{ steps.run.memory_kb }}",
        "summary: ${{ steps.compile.compile_log }}",
    ):
        assert wiring in definition
    assert files["README.md"].startswith(b"# classic\n")
