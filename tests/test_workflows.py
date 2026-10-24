"""Seeding the built-in workflows, unicon/classic, unicon/classic-folder
and unicon/interactive, at each of their versions, each version's whole
folder: what is made on a fresh forge, what is left alone on a rerun, the
shape the forge package looks for, and the one way a version is ever
rewritten.

The forge package finds a workflow as a public repository
`<owner>/<name>.workflow` carrying the topic `unicon-workflow`, with a git tag
per version, so these tests hold the names the seeding has to produce. They
run bootstrap's seeding step against the in-memory Forgejo of forgejo_fake.py.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from bootstrap import main
from bootstrap.forgejo import Forgejo, ForgejoError
from bootstrap.images import LocalSource, Manifest, PrimitiveRelease
from bootstrap.platform_repos import PlatformRepos, VersionDiffers, publish
from bootstrap.summary import Summary
from bootstrap.workflows import (
    CLASSIC,
    WorkflowError,
    graded,
    primitives_used,
    seed_files,
    seed_versions,
    seeded,
)
from tests.forgejo_fake import FakeForgejo

DEPLOY = Path(__file__).resolve().parent.parent
ORG = "unicon"
FULL = f"{ORG}/{CLASSIC}.workflow"
REPO = f"/repos/{FULL}"
FOLDER = "classic-folder"
FOLDER_REPO = f"/repos/{ORG}/{FOLDER}.workflow"
INTERACTIVE = "interactive"
INTERACTIVE_REPO = f"/repos/{ORG}/{INTERACTIVE}.workflow"
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


FIRST = {"workflow.yaml": b"steps: [0]\n", "README.md": b"# classic\n"}
"""A first version of classic as an older deploy directory held it."""


def _v2() -> dict[str, bytes]:
    return seed_files(DEPLOY, CLASSIC, "v2")


def _deploy_with(tmp_path: Path, definitions: dict[str, bytes]) -> Path:
    """A deploy directory whose classic versions are these definitions, and
    no other workflow.
    """
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


def test_a_fresh_forge_gets_each_workflow_at_each_version() -> None:
    forge = FakeForgejo()

    summary = _seed(forge)

    assert summary.lines()[0] == "created: 11, already present: 0"
    assert forge.writes() == [
        ("POST", "/orgs"),
        ("POST", f"/orgs/{ORG}/repos"),
        ("POST", f"{REPO}/contents"),
        ("POST", f"{REPO}/tags"),
        ("POST", f"{REPO}/contents"),
        ("POST", f"{REPO}/tags"),
        ("PUT", f"{REPO}/topics/unicon-workflow"),
        ("POST", f"/orgs/{ORG}/repos"),
        ("POST", f"{FOLDER_REPO}/contents"),
        ("POST", f"{FOLDER_REPO}/tags"),
        ("PUT", f"{FOLDER_REPO}/topics/unicon-workflow"),
        ("POST", f"/orgs/{ORG}/repos"),
        ("POST", f"{INTERACTIVE_REPO}/contents"),
        ("POST", f"{INTERACTIVE_REPO}/tags"),
        ("PUT", f"{INTERACTIVE_REPO}/topics/unicon-workflow"),
    ]
    folder = forge.repos[f"{ORG}/{FOLDER}.workflow"]
    assert list(folder.tags) == ["v1"]
    assert folder.files_at("v1") == seed_files(DEPLOY, FOLDER, "v1")
    assert f"  created  workflow {ORG}/{FOLDER} version v1" in summary.lines()
    repo = forge.repos[FULL]
    assert list(repo.tags) == ["v2", "v3"]
    assert repo.files_at("v2") == _v2()
    assert set(repo.files_at("v3")) == {
        "workflow.yaml",
        "README.md",
        "diff/diff.py",
        "diff/program.yaml",
    }
    assert repo.topics == ["unicon-workflow"]
    assert f"  created  workflow {ORG}/{CLASSIC} version v2" in summary.lines()
    assert f"  created  workflow {ORG}/{CLASSIC} version v3" in summary.lines()
    assert not any(f"{ORG}/{CLASSIC} version v1" in line for line in summary.lines())
    interactive = forge.repos[f"{ORG}/{INTERACTIVE}.workflow"]
    assert set(interactive.files_at("v1")) == {
        "workflow.yaml",
        "README.md",
        "verdict/verdict.py",
        "verdict/program.yaml",
    }


def test_a_seeded_forge_is_read_and_not_written() -> None:
    forge = FakeForgejo()
    _seed(forge)
    forge.requests.clear()

    summary = _seed(forge)

    assert summary.lines()[0] == "created: 0, already present: 11"
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

    made = [
        body
        for method, path, body in forge.bodies
        if (method, path) == ("POST", f"/orgs/{ORG}/repos")
    ]
    assert made == [
        {
            "name": f"{name}.workflow",
            "private": False,
            "default_branch": "main",
            "auto_init": False,
        }
        for name in (CLASSIC, FOLDER, INTERACTIVE)
    ]


def test_each_version_lands_in_one_commit_that_its_tag_points_at(
    tmp_path: Path,
) -> None:
    forge = FakeForgejo()
    directory = _deploy_with(tmp_path, {"v1": b"steps: [1]\n", "v2": b"steps: [2]\n"})

    _seed(forge, directory)

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
    } == seed_files(directory, CLASSIC, "v1")
    assert all(entry["operation"] == "create" for entry in first["files"])
    assert "new_branch" not in second
    repo = forge.repos[FULL]
    assert repo.tags["v2"] == repo.head
    assert repo.commits[repo.tags["v1"]] == seed_files(directory, CLASSIC, "v1")


def test_a_commit_holding_the_files_without_a_tag_is_tagged_where_it_is() -> None:
    """An earlier run that stopped between the commit and the tag."""
    forge = FakeForgejo()
    repo = forge.seed(FULL, _v2(), topics=("unicon-workflow",))
    head = repo.head

    _seed(forge)

    assert repo.tags["v2"] == head
    assert repo.files_at("v2") == _v2()


def test_a_changed_v1_stops_the_run_before_v2_is_made(tmp_path: Path) -> None:
    forge = FakeForgejo()
    repo = forge.seed(FULL, FIRST, tags=("v1",), topics=("unicon-workflow",))
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
    _seed(
        forge,
        _deploy_with(tmp_path / "a", {"v1": b"steps: [1]\n", "v2": b"steps: [2]\n"}),
    )
    directory = _deploy_with(
        tmp_path / "b", {"v1": b"steps: [3]\n", "v2": b"steps: [4]\n"}
    )
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
    repo = forge.seed(FULL, FIRST, tags=("v1",))
    forge.requests.clear()
    repos = PlatformRepos(Forgejo(forge, "forgejo"), TOKEN)

    with pytest.raises(VersionDiffers, match=f"{FULL}@v1 at the forge differs"):
        publish(repos, ORG, f"{CLASSIC}.workflow", "v1", _v2(), message="m")

    assert forge.writes() == []
    assert repo.files_at("v1") == FIRST


def test_rewrite_moves_each_version_that_differs(tmp_path: Path) -> None:
    forge = FakeForgejo()
    _seed(
        forge,
        _deploy_with(tmp_path / "a", {"v1": b"steps: [1]\n", "v2": b"steps: [2]\n"}),
    )
    repo = forge.repos[FULL]
    old = dict(repo.tags)
    directory = _deploy_with(
        tmp_path / "b", {"v1": b"steps: [1]\n", "v2": b"steps: [3]\n"}
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


def _without_form_text(definition: bytes) -> bytes:
    """classic's definition as it was before its inputs gave the task's form
    a title, help and unit, and its reported values a title and unit.
    """
    kept = [
        line
        for line in definition.decode().splitlines(keepends=True)
        if line.strip().split(":")[0] not in ("title", "help", "unit")
    ]
    return "".join(kept).encode()


@pytest.mark.parametrize(
    "older",
    [
        {**_v2(), "workflow.yaml": _without_form_text(_v2()["workflow.yaml"])},
        {**_v2(), "README.md": b"# classic, as it read before\n"},
    ],
    ids=["its form text", "its README"],
)
def test_a_version_changed_in_nothing_that_grades_moves_to_the_new_files(
    older: dict[str, bytes],
) -> None:
    forge = FakeForgejo()
    repo = forge.seed(FULL, older, tags=("v2",), topics=("unicon-workflow",))
    before = repo.tags["v2"]

    summary = _seed(forge)

    assert repo.tags["v2"] != before
    assert repo.files_at("v2") == _v2()
    assert before in repo.commits
    assert any(
        f"workflow {ORG}/{CLASSIC} version v2" in line and "moved" in line
        for line in summary.lines()
    )
    forge.requests.clear()
    _seed(forge)
    assert forge.writes() == []


RELEASED = Path(__file__).resolve().parent / "released"
"""Each built-in workflow version as a deploy release published it, copied
byte for byte from its tag: v0.7.1's write their inputs in the short form,
`time_limit: number`, and v0.9.0's give their inputs a title, help and
unit but their reported values none, `log` a bare reference, as the stacks
bootstrapped from each hold them.
"""


def _released(release: str, name: str, version: str) -> dict[str, bytes]:
    root = RELEASED / release / name / version
    return {path: (root / path).read_bytes() for path in ("workflow.yaml", "README.md")}


@pytest.mark.parametrize("release", ["v0.7.1", "v0.9.0"])
def test_the_versions_a_release_published_move_to_their_form_text(release: str) -> None:
    forge = FakeForgejo()
    classic = forge.seed(
        FULL,
        _released(release, CLASSIC, "v2"),
        tags=("v2",),
        topics=("unicon-workflow",),
    )
    folder = forge.seed(
        f"{ORG}/{FOLDER}.workflow",
        _released(release, FOLDER, "v1"),
        tags=("v1",),
        topics=("unicon-workflow",),
    )

    summary = _seed(forge)

    assert classic.files_at("v2") == _v2()
    assert folder.files_at("v1") == seed_files(DEPLOY, FOLDER, "v1")
    for name, version in ((CLASSIC, "v2"), (FOLDER, "v1")):
        assert any(
            f"workflow {ORG}/{name} version {version}" in line and "moved" in line
            for line in summary.lines()
        )


@pytest.mark.parametrize(
    ("older", "newer"),
    [
        (b"inputs:\n  limit: number\n", b"inputs:\n  limit: {type: number, unit: s}\n"),
        (b"test:\n  answer: file\n", b"test:\n  answer: {type: file}\n"),
        (
            b"inputs:\n  limit: number\n",
            b"inputs:\n  limit: {type: number, optional: false}\n",
        ),
        (
            b"steps:\n  - {id: a, use: x}\n",
            b"steps:\n  - {id: a, use: x, per_test: false}\n",
        ),
        (
            b"report:\n  log: ${{ steps.c.log }}\n",
            b"report:\n  log: {from: '${{ steps.c.log }}', title: Compile log}\n",
        ),
        (
            b"report:\n  t: {from: x, fold: max}\n",
            b"report:\n  t: {from: x, title: Run time, unit: ms, fold: max}\n",
        ),
    ],
    ids=[
        "a short input",
        "a short test field",
        "a false flag",
        "a false step flag",
        "a short reported value",
        "a reported value's title and unit",
    ],
)
def test_a_declaration_reads_as_the_forge_reads_it(older: bytes, newer: bytes) -> None:
    assert graded(older) == graded(newer)


def test_a_flag_set_true_still_grades_differently() -> None:
    assert graded(b"inputs:\n  limit: number\n") != graded(
        b"inputs:\n  limit: {type: number, optional: true}\n"
    )


def test_a_reported_values_meaning_still_grades_differently() -> None:
    assert graded(b"report:\n  t: {from: x, title: T, fold: max}\n") != graded(
        b"report:\n  t: {from: x, title: T, fold: sum}\n"
    )


def test_a_version_changed_in_what_grades_still_stops_the_run() -> None:
    forge = FakeForgejo()
    changed = _v2()["workflow.yaml"].replace(b"fold: max", b"fold: sum", 1)
    forge.seed(FULL, {**_v2(), "workflow.yaml": changed}, tags=("v2",))
    forge.requests.clear()

    with pytest.raises(main.DifferingVersions, match=f"{ORG}/{CLASSIC}@v2"):
        _seed(forge)

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
    assert list(seed_versions(DEPLOY, CLASSIC)) == ["v2", "v3"]


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
    assert definition[at("inputs:") : at("test:")] == [
        "inputs:",
        "  submission:",
        "    type: file",
        "    contestant: true",
        "    title: Solution",
        "    help: The source file the contestant submits.",
        "  language:",
        "    type: enum",
        "    options: [c, cpp, java, python]",
        "    contestant: true",
        "    title: Language",
        "    help: The languages a contestant may write their solution in.",
        "  time_limit:",
        "    type: number",
        "    title: Time limit",
        "    unit: seconds",
        "    help: The CPU time the program may use on each test.",
        "  memory_limit:",
        "    type: number",
        "    title: Memory limit",
        "    unit: MB",
        "    help: The memory the program may use on each test.",
    ]
    assert definition[at("test:") : at("test:") + 3] == [
        "test:",
        "  input: file",
        "  answer: file",
    ]
    assert definition[at("report:") :] == [
        "report:",
        "  time_ms:",
        "    from: ${{ steps.run.time_ms }}",
        "    title: Run time",
        "    unit: ms",
        "    fold: max",
        "    better: lower",
        "    at_least: 0",
        "  memory_kb:",
        "    from: ${{ steps.run.memory_kb }}",
        "    title: Memory used",
        "    unit: KB",
        "    fold: max",
        "    better: lower",
        "    at_least: 0",
        "  log:",
        "    from: ${{ steps.compile.compile_log }}",
        "    title: Compile log",
    ]
    for wiring in (
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


def _pinning(**versions: tuple[str, ...]) -> Manifest:
    """A manifest pinning each primitive named at these versions."""
    return Manifest(
        path=DEPLOY / "images.json",
        images=NO_PRIMITIVES.images,
        primitives=tuple(
            PrimitiveRelease(
                name.replace("_", "-"),
                version,
                f"ghcr.io/uniconhq/primitive-{name.replace('_', '-')}{DIGEST}",
                LocalSource(DEPLOY.parent / f"primitive-{name.replace('_', '-')}"),
            )
            for name, pinned in versions.items()
            for version in pinned
        ),
    )


EVERY_USE = {
    "compile": ("v2", "v3"),
    "sandbox_run": ("v2",),
    "diff_check": ("v2",),
    "exec": ("v1",),
}
"""Every primitive version the built-in workflows use."""


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
    assert primitives_used(_v2()["workflow.yaml"]) == [
        "unicon/compile@v2",
        "unicon/sandbox-run@v2",
        "unicon/diff-check@v2",
    ]


def test_every_primitive_version_the_workflows_use_has_to_be_pinned() -> None:
    main.refuse_unpinned_primitives(DEPLOY, _pinning(**EVERY_USE))

    with pytest.raises(main.UnpinnedPrimitive) as refused:
        main.refuse_unpinned_primitives(
            DEPLOY, _pinning(**{**EVERY_USE, "compile": ("v2",)})
        )

    message = str(refused.value)
    assert message.startswith("images.json does not pin every primitive version")
    assert (
        f"workflows/{CLASSIC}/v3/ uses unicon/compile@v3; "
        f"workflows/{CLASSIC}/v3/ uses unicon/compile@v3; "
        f"workflows/{INTERACTIVE}/v1/ uses unicon/compile@v3; "
        f"workflows/{INTERACTIVE}/v1/ uses unicon/compile@v3; "
        f"workflows/{INTERACTIVE}/v1/ uses unicon/compile@v3."
    ) in message
    assert f"{CLASSIC}/v2/" not in message


def test_the_workflows_are_the_folders_under_workflows(tmp_path: Path) -> None:
    assert seeded(DEPLOY) == [CLASSIC, FOLDER, INTERACTIVE]
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "README.md").write_bytes(b"")

    with pytest.raises(WorkflowError, match="not a workflow folder"):
        seeded(tmp_path)


def test_classic_folder_compiles_a_folder_from_its_entry_with_the_v2_primitives() -> (
    None
):
    files = seed_files(DEPLOY, FOLDER, "v1")

    assert set(files) == {"workflow.yaml", "README.md"}
    definition = files["workflow.yaml"].decode().splitlines()
    assert definition[0].startswith("# unicon/classic-folder@v1,")
    assert primitives_used(files["workflow.yaml"]) == [
        "unicon/compile@v2",
        "unicon/sandbox-run@v2",
        "unicon/diff-check@v2",
    ]
    at = definition.index
    assert definition[at("  program:") : at("  language:")] == [
        "  program:",
        "    type: folder",
        "    contestant: true",
        "    title: Program",
        "    help: The folder of source files the contestant submits.",
    ]
    assert definition[at("  entry:") : at("  entry:") + 3] == [
        "  entry:",
        "    type: text",
        "    contestant: true",
    ]
    for wiring in (
        "      source: ${{ inputs.program }}",
        "      entry: ${{ inputs.entry }}",
        "      binary: ${{ steps.compile.binary }}",
    ):
        assert wiring in definition
    assert files["README.md"].startswith(b"# classic-folder\n")


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


def test_a_version_is_seeded_with_its_whole_folder_but_pythons_caches(
    tmp_path: Path,
) -> None:
    directory = _deploy_with(tmp_path, {"v1": b"steps: [1]\n"})
    root = directory / "workflows" / CLASSIC / "v1"
    (root / "diff" / "__pycache__").mkdir(parents=True)
    (root / "diff" / "diff.py").write_bytes(b"print(1)\n")
    (root / "diff" / "__pycache__" / "diff.cpython-314.pyc").write_bytes(b"\0")
    (root / "stray.pyc").write_bytes(b"\0")

    assert seed_files(directory, CLASSIC, "v1") == {
        "README.md": b"# classic\n",
        "diff/diff.py": b"print(1)\n",
        "workflow.yaml": b"steps: [1]\n",
    }
    assert set(seed_files(DEPLOY, CLASSIC, "v3")) == {
        "workflow.yaml",
        "README.md",
        "diff/diff.py",
        "diff/program.yaml",
    }


def test_a_file_git_ignores_and_the_finders_are_left_out_of_a_version(
    tmp_path: Path,
) -> None:
    directory = _deploy_with(tmp_path, {"v1": b"steps: [1]\n"})
    root = directory / "workflows" / CLASSIC / "v1"
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    subprocess.run(["git", "-C", str(directory), "add", "workflows"], check=True)
    (directory / ".gitignore").write_text("*.log\n")
    (root / ".DS_Store").write_bytes(b"\0")
    (root / "run.log").write_bytes(b"local\n")
    (root / "check.py").write_bytes(b"print(1)\n")

    assert seed_files(directory, CLASSIC, "v1") == {
        "README.md": b"# classic\n",
        "check.py": b"print(1)\n",
        "workflow.yaml": b"steps: [1]\n",
    }
    # Outside a git checkout only the Finder's file is left out.
    shutil.rmtree(directory / ".git")
    assert set(seed_files(directory, CLASSIC, "v1")) == {
        "README.md",
        "check.py",
        "run.log",
        "workflow.yaml",
    }


def test_a_link_in_a_version_folder_is_refused(tmp_path: Path) -> None:
    directory = _deploy_with(tmp_path, {"v1": b"steps: [1]\n"})
    root = directory / "workflows" / CLASSIC / "v1"
    (root / "answers").symlink_to("/etc")

    with pytest.raises(WorkflowError, match="is a link"):
        seed_files(directory, CLASSIC, "v1")


def test_a_version_without_its_readme_is_refused(tmp_path: Path) -> None:
    directory = _deploy_with(tmp_path, {"v1": b"steps: [1]\n"})
    (directory / "workflows" / CLASSIC / "v1" / "README.md").unlink()

    with pytest.raises(WorkflowError, match=r"holds no README\.md"):
        seed_files(directory, CLASSIC, "v1")


def test_a_changed_script_stops_the_run_though_the_definition_is_the_same() -> None:
    forge = FakeForgejo()
    v3 = seed_files(DEPLOY, CLASSIC, "v3")
    older = {**v3, "diff/diff.py": v3["diff/diff.py"] + b"# an older script\n"}
    forge.seed(FULL, older, tags=("v3",), topics=("unicon-workflow",))
    forge.requests.clear()

    with pytest.raises(main.DifferingVersions) as refused:
        _seed(forge)

    assert forge.writes() == []
    assert f"{ORG}/{CLASSIC}@v3, against workflows/{CLASSIC}/v3/" in str(refused.value)


def test_a_run_workflow_changed_only_in_an_outputs_words_moves_in_place() -> None:
    forge = FakeForgejo()
    v3 = seed_files(DEPLOY, CLASSIC, "v3")
    definition = v3["workflow.yaml"].replace(b"title: Run time, ", b"")
    assert definition != v3["workflow.yaml"]
    repo = forge.seed(
        FULL,
        {**v3, "workflow.yaml": definition},
        tags=("v3",),
        topics=("unicon-workflow",),
    )
    before = repo.tags["v3"]

    summary = _seed(forge)

    assert repo.tags["v3"] != before
    assert repo.files_at("v3") == v3
    assert any(
        f"workflow {ORG}/{CLASSIC} version v3" in line and "moved" in line
        for line in summary.lines()
    )
    assert graded(definition) == graded(v3["workflow.yaml"])
    assert graded(definition.replace(b"steps.solution.time_ms", b"steps.x.y")) != (
        graded(v3["workflow.yaml"])
    )
