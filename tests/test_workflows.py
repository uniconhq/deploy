"""Seeding unicon/classic at each of its versions: what is made on a fresh
forge, what is left alone on a rerun, the shape the forge package looks for,
and the one way a version is ever rewritten.

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
from bootstrap.forgejo import Forgejo, ForgejoError
from bootstrap.images import LocalSource, Manifest, PrimitiveRelease
from bootstrap.platform_repos import PlatformRepos, VersionDiffers, publish
from bootstrap.summary import Summary
from bootstrap.workflows import (
    CLASSIC,
    SEED_FILES,
    WorkflowError,
    primitives_used,
    seed_files,
    seed_versions,
)
from tests.forgejo_fake import FakeForgejo

DEPLOY = Path(__file__).resolve().parent.parent
ORG = "unicon"
FULL = f"{ORG}/{CLASSIC}.workflow"
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
REPORTED = "fold: max, better: lower, at_least: 0}"


def _v1() -> dict[str, bytes]:
    return seed_files(DEPLOY, CLASSIC, "v1")


def _v2() -> dict[str, bytes]:
    return seed_files(DEPLOY, CLASSIC, "v2")


def _deploy_with(tmp_path: Path, definitions: dict[str, bytes]) -> Path:
    """A deploy directory whose classic versions are these definitions."""
    for version, definition in definitions.items():
        root = tmp_path / "workflows" / CLASSIC / version
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


def test_a_fresh_forge_gets_v1_then_v2() -> None:
    forge = FakeForgejo()

    summary = _seed(forge)

    assert summary.lines()[0] == "created: 5, already present: 0"
    assert forge.writes() == [
        ("POST", "/orgs"),
        ("POST", f"/orgs/{ORG}/repos"),
        ("POST", f"{REPO}/contents"),
        ("POST", f"{REPO}/tags"),
        ("POST", f"{REPO}/contents"),
        ("POST", f"{REPO}/tags"),
        ("PUT", f"{REPO}/topics/unicon-workflow"),
    ]
    repo = forge.repos[FULL]
    assert list(repo.tags) == ["v1", "v2"]
    assert repo.files_at("v1") == _v1()
    assert repo.files_at("v2") == _v2()
    assert repo.topics == ["unicon-workflow"]
    assert f"  created  workflow {ORG}/{CLASSIC} version v1" in summary.lines()
    assert f"  created  workflow {ORG}/{CLASSIC} version v2" in summary.lines()


def test_a_seeded_forge_is_read_and_not_written() -> None:
    forge = FakeForgejo()
    _seed(forge)
    forge.requests.clear()

    summary = _seed(forge)

    assert summary.lines()[0] == "created: 0, already present: 5"
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


def test_each_version_lands_in_one_commit_that_its_tag_points_at() -> None:
    forge = FakeForgejo()

    _seed(forge)

    commits = [
        body
        for method, path, body in forge.bodies
        if (method, path) == ("POST", f"{REPO}/contents")
    ]
    assert [commit["message"] for commit in commits] == [
        f"Seed {ORG}/{CLASSIC}@v1",
        f"Seed {ORG}/{CLASSIC}@v2",
    ]
    first, second = commits
    assert first["branch"] == "main"
    assert first["new_branch"] == "main"
    assert {
        entry["path"]: base64.b64decode(entry["content"]) for entry in first["files"]
    } == _v1()
    assert all(entry["operation"] == "create" for entry in first["files"])
    assert "new_branch" not in second
    repo = forge.repos[FULL]
    assert repo.tags["v2"] == repo.head
    assert repo.commits[repo.tags["v1"]] == _v1()


def test_a_commit_holding_the_files_without_a_tag_is_tagged_where_it_is() -> None:
    """An earlier run that stopped between the commit and the tag."""
    forge = FakeForgejo()
    repo = forge.seed(FULL, _v1(), topics=("unicon-workflow",))
    head = repo.head

    _seed(forge)

    assert repo.tags["v1"] == head
    assert repo.files_at("v2") == _v2()


def test_a_changed_v1_stops_the_run_before_v2_is_made(tmp_path: Path) -> None:
    forge = FakeForgejo()
    repo = forge.seed(FULL, _v1(), tags=("v1",), topics=("unicon-workflow",))
    first = repo.tags["v1"]
    directory = _deploy_with(tmp_path, {"v1": b"steps: [1]\n", "v2": b"steps: [2]\n"})
    forge.requests.clear()

    with pytest.raises(main.DifferingVersions) as refused:
        _seed(forge, directory)

    assert forge.writes() == []
    assert list(repo.tags) == ["v1"]
    assert repo.tags["v1"] == first
    message = str(refused.value)
    assert f"{ORG}/{CLASSIC}@v1, against workflows/{CLASSIC}/v1/" in message
    assert "new version folder" in message


def test_every_version_that_differs_is_named(tmp_path: Path) -> None:
    forge = FakeForgejo()
    _seed(forge)
    directory = _deploy_with(tmp_path, {"v1": b"steps: [1]\n", "v2": b"steps: [2]\n"})
    forge.requests.clear()

    with pytest.raises(main.DifferingVersions) as refused:
        _seed(forge, directory)

    assert forge.writes() == []
    assert str(refused.value).splitlines()[1:3] == [
        f"  {ORG}/{CLASSIC}@v1, against workflows/{CLASSIC}/v1/",
        f"  {ORG}/{CLASSIC}@v2, against workflows/{CLASSIC}/v2/",
    ]


def test_publishing_over_a_version_that_differs_is_refused() -> None:
    forge = FakeForgejo()
    repo = forge.seed(FULL, _v1(), tags=("v1",))
    forge.requests.clear()
    repos = PlatformRepos(Forgejo(forge, "forgejo"), TOKEN)

    with pytest.raises(VersionDiffers, match=f"{FULL}@v1 at the forge differs"):
        publish(repos, ORG, f"{CLASSIC}.workflow", "v1", _v2(), message="m")

    assert forge.writes() == []
    assert repo.files_at("v1") == _v1()


def test_rewrite_moves_each_version_that_differs(tmp_path: Path) -> None:
    forge = FakeForgejo()
    _seed(forge)
    repo = forge.repos[FULL]
    old = dict(repo.tags)
    directory = _deploy_with(
        tmp_path, {"v1": _v1()["workflow.yaml"], "v2": b"steps: [2]\n"}
    )
    (directory / "workflows" / CLASSIC / "v1" / "README.md").write_bytes(
        _v1()["README.md"]
    )

    summary = _seed(forge, directory, rewrite=True)

    assert repo.tags["v1"] == old["v1"]
    assert repo.tags["v2"] != old["v2"]
    assert repo.files_at("v2") == seed_files(directory, CLASSIC, "v2")
    assert old["v2"] in repo.commits
    assert f"  created  workflow {ORG}/{CLASSIC} version v2 (rewritten)" in (
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


def test_the_versions_are_the_version_folders_oldest_first(tmp_path: Path) -> None:
    directory = _deploy_with(tmp_path, {"v10": b"ten\n", "v2": b"two\n", "v1": b"1\n"})

    versions = seed_versions(directory, CLASSIC)

    assert list(versions) == ["v1", "v2", "v10"]
    assert versions["v10"]["workflow.yaml"] == b"ten\n"
    assert list(seed_versions(DEPLOY, CLASSIC)) == ["v1", "v2"]


@pytest.mark.parametrize("stray", ["workflow.yaml", "latest"])
def test_anything_but_a_version_folder_is_refused(tmp_path: Path, stray: str) -> None:
    directory = _deploy_with(tmp_path, {"v1": b"1\n"})
    path = directory / "workflows" / CLASSIC / stray
    if stray == "latest":
        path.mkdir()
    else:
        path.write_bytes(b"")

    with pytest.raises(WorkflowError, match="not a version folder"):
        seed_versions(directory, CLASSIC)


def test_v1_wires_the_v1_primitives() -> None:
    files = _v1()

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


def test_v2_wires_the_v2_primitives_over_tests_and_reports() -> None:
    files = _v2()

    assert set(files) == {"workflow.yaml", "README.md"}
    lines = files["workflow.yaml"].decode().splitlines()
    assert lines[0].startswith("# unicon/classic@v2,")
    definition = [line for line in lines if not line.startswith("#")]
    assert not any(line.startswith(("name:", "version:")) for line in definition)
    assert [line.split("use: ")[1] for line in definition if "use: " in line] == [
        "unicon/compile@v2",
        "unicon/sandbox-run@v2",
        "unicon/diff-check@v2",
    ]
    at = definition.index
    assert definition[at("test:") : at("test:") + 3] == [
        "test:",
        "  input: file",
        "  answer: file",
    ]
    assert definition[at("report:") :] == [
        "report:",
        f'  time_ms: {{from: "${{{{ steps.run.time_ms }}}}", {REPORTED}',
        f'  memory_kb: {{from: "${{{{ steps.run.memory_kb }}}}", {REPORTED}',
        "  log: ${{ steps.compile.compile_log }}",
    ]
    for wiring in (
        "  submission: {type: file, contestant: true}",
        "  language: {type: enum, options: [c, cpp, java, python], contestant: true}",
        "      source: ${{ inputs.submission }}",
        "      language: ${{ inputs.language }}",
        "      binary: ${{ steps.compile.binary }}",
        "      input: ${{ test.input }}",
        "      actual: ${{ steps.run.output }}",
        "      expected: ${{ test.answer }}",
    ):
        assert wiring in definition
    assert definition.count("    per_test: true") == 2
    assert files["README.md"].startswith(b"# classic\n")


def _pinning(*versions: str) -> Manifest:
    """A manifest pinning each of the three primitives at these versions."""
    return Manifest(
        path=DEPLOY / "images.json",
        images=NO_PRIMITIVES.images,
        primitives=tuple(
            PrimitiveRelease(
                name,
                version,
                f"ghcr.io/uniconhq/primitive-{name}{DIGEST}",
                LocalSource(DEPLOY.parent / f"primitive-{name}"),
            )
            for name in ("compile", "diff-check", "sandbox-run")
            for version in versions
        ),
    )


def test_the_primitives_a_definition_uses_are_read_from_its_steps() -> None:
    definition = b"""# use: unicon/commented@v9
steps:
  - id: a
    use: unicon/compile@v2
  - {id: b, use: "unicon/sandbox-run@v2", per_test: true}
  - id: c
    use: 'unicon/diff-check@v2'  # the comparison
    with:
      reuse: ${{ steps.a.binary }}
"""

    assert primitives_used(definition) == [
        "unicon/compile@v2",
        "unicon/sandbox-run@v2",
        "unicon/diff-check@v2",
    ]
    assert primitives_used(_v1()["workflow.yaml"]) == [
        "unicon/compile@v1",
        "unicon/sandbox-run@v1",
        "unicon/diff-check@v1",
    ]


def test_every_primitive_version_the_workflow_uses_has_to_be_pinned() -> None:
    main.refuse_unpinned_primitives(DEPLOY, _pinning("v1", "v2"))

    with pytest.raises(main.UnpinnedPrimitive) as refused:
        main.refuse_unpinned_primitives(DEPLOY, _pinning("v1"))

    message = str(refused.value)
    assert message.startswith("images.json does not pin every primitive version")
    assert (
        f"workflows/{CLASSIC}/v2/ uses unicon/compile@v2; "
        f"workflows/{CLASSIC}/v2/ uses unicon/sandbox-run@v2; "
        f"workflows/{CLASSIC}/v2/ uses unicon/diff-check@v2."
    ) in message
    assert "v1/" not in message


def test_a_manifest_pinning_no_primitive_seeds_no_workflow() -> None:
    with pytest.raises(main.UnpinnedPrimitive, match=r"scripts/build-images\.py"):
        main.refuse_unpinned_primitives(DEPLOY, NO_PRIMITIVES)


def test_bootstrap_stops_at_an_unpinned_primitive_before_writing_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (".env.example", "images.json"):
        (tmp_path / name).write_bytes((DEPLOY / name).read_bytes())
    _deploy_with(tmp_path, {"v1": b"steps:\n  - use: unicon/compile@v7\n"})

    status = main.main(["--directory", str(tmp_path), "-f", "compose.yaml"])

    assert status == 1
    assert "uses unicon/compile@v7" in capsys.readouterr().out
    assert not (tmp_path / ".env").exists()
