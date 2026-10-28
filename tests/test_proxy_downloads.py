"""The download door's patterns in the proxy's config, read from the file as
nginx reads them: for each of its two kinds of address, the location a
download is asked at, and the map that lets through only the forge's own
answer for it. A submission's files sit under `files/<input>/`: a folder
input's in folders of their own, and each part of an input given once per
tuple under `<part>/`, its tuple's position from 1. The forge names each at
the submission's commit, so both patterns take every such path and nothing
else. A task's own files are asked for at
`versions/<commit>/<path>`, any file of the task at any depth, and the forge
names each in the task's own repository at that commit. A workflow
version's files are asked for at
`workflows/<owner>/<name>/versions/<version>/<path>`, the version's own name,
and the forge names each in the workflow's repository at the commit that
name stands at, and a workflow draft's at
`workflows/<owner>/<name>/draft/<path>`, at the draft's head. No kind's
patterns take another's addresses. Everything else
the locations do is written once, in `download.conf`, which compose mounts
where they include it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

DEPLOY = Path(__file__).resolve().parent.parent
CONFIG = (DEPLOY / "proxy" / "nginx.conf").read_text()
SHARED = (DEPLOY / "proxy" / "download.conf").read_text()
SHARED_AT = "/etc/nginx/download.conf"


def _locations() -> list[str]:
    return re.findall(r'location ~ "(\^/-/downloads/[^"]+)"', CONFIG)


def _location() -> re.Pattern[str]:
    """The submission's location, the one whose fourth part is a number."""
    found = [
        pattern
        for pattern in _locations()
        if "/versions/" not in pattern
        and "/entrants/" not in pattern
        and "/workflows/" not in pattern
    ]
    assert len(found) == 1, found
    return re.compile(found[0])


def _staff_location() -> re.Pattern[str]:
    """A staff download of an entrant's submitted file."""
    found = [pattern for pattern in _locations() if "/entrants/" in pattern]
    assert len(found) == 1, found
    return re.compile(found[0])


def _task_location() -> re.Pattern[str]:
    found = [
        pattern
        for pattern in _locations()
        if "/versions/" in pattern and "/workflows/" not in pattern
    ]
    assert len(found) == 1, found
    return re.compile(found[0])


def _workflow_location() -> re.Pattern[str]:
    found = [
        pattern
        for pattern in _locations()
        if "/workflows/" in pattern and "/versions/" in pattern
    ]
    assert len(found) == 1, found
    return re.compile(found[0])


def _draft_location() -> re.Pattern[str]:
    found = [pattern for pattern in _locations() if "/draft" in pattern]
    assert len(found) == 1, found
    return re.compile(found[0])


def _map_of(variable: str) -> re.Pattern[str]:
    block = CONFIG[CONFIG.index(f"map ${variable} ") :]
    found = re.search(r'"~(\^/api/v1/repos/[^"]+)"', block)
    assert found is not None
    return re.compile(found.group(1))


def _map() -> re.Pattern[str]:
    return _map_of("download_path")


def _task_map() -> re.Pattern[str]:
    return _map_of("task_file_path")


def _workflow_map() -> re.Pattern[str]:
    return _map_of("workflow_file_path")


SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize(
    "path",
    [
        "files/submission/main.py",
        "files/route/1/route.txt",
        "files/program/src/util/io.c",
    ],
)
def test_every_file_a_submission_holds_is_a_download(path: str) -> None:
    asked = f"/-/downloads/acme/spring/sum/3/{path}"
    door = f"/api/v1/repos/k1/k2.sub/media/{path}?ref={SHA}"

    assert _location().match(asked), asked
    assert _map().match(door), door


@pytest.mark.parametrize(
    "door",
    [
        f"/api/v1/repos/k1/k2.sub/media/submission.json?ref={SHA}",
        f"/api/v1/repos/k1/k2.sub/raw/files/a/b?ref={SHA}",
        "/api/v1/repos/k1/k2.sub/media/files/a/b?ref=main",
        f"/api/v1/repos/k1/k2.sub/media/files/a/../b?ref={SHA}",
        f"/api/v1/repos/k1/k2.sub/media/files/a?ref={SHA}",
        f"/api/v1/repos/k1/k2.sum.task/media/files/a/b?ref={SHA}",
    ],
)
def test_nothing_but_a_file_under_files_at_a_commit_passes_the_map(door: str) -> None:
    assert not _map().match(door), door


@pytest.mark.parametrize(
    "asked",
    [
        "/-/downloads/acme/spring/sum/3/submission.json",
        "/-/downloads/acme/spring/sum/3/files/a",
        "/-/downloads/acme/spring/sum/x/files/a/b",
    ],
)
def test_nothing_but_a_file_under_files_is_asked_at_the_door(asked: str) -> None:
    assert not _location().match(asked), asked


@pytest.mark.parametrize(
    "path",
    [
        "task.yaml",
        "statement.md",
        "tests/1.ans",
        "plans/plan.json",
        "public/a%20b.png",
        "data/x/y/z/weights.bin",
    ],
)
def test_every_file_a_task_holds_is_a_download_at_a_commit(path: str) -> None:
    for commit in (SHA, SHA + "0" * 24):
        asked = f"/-/downloads/acme/spring/sum/versions/{commit}/{path}"
        door = f"/api/v1/repos/k1/spring.sum.task/media/{path}?ref={commit}"

        assert _task_location().match(asked), asked
        assert _task_map().match(door), door


@pytest.mark.parametrize(
    "door",
    [
        f"/api/v1/repos/k1/spring.sum.task/raw/task.yaml?ref={SHA}",
        "/api/v1/repos/k1/spring.sum.task/media/task.yaml?ref=main",
        f"/api/v1/repos/k1/spring.sum.task/media/task.yaml?ref={SHA.upper()}",
        f"/api/v1/repos/k1/spring.sum.task/media/a/../task.yaml?ref={SHA}",
        f"/api/v1/repos/k1/spring.sum.task/media/./task.yaml?ref={SHA}",
        f"/api/v1/repos/k1/spring.sum.task/media?ref={SHA}",
        f"/api/v1/repos/k1/spring.sum.task/media/task.yaml?ref={SHA}&x=1",
        f"/api/v1/repos/k1/spring.contest/media/contest.yaml?ref={SHA}",
        f"/api/v1/repos/k1/spring.sum.k2.sub/media/files/a/b?ref={SHA}",
        f"/api/v1/repos/k1/classic.workflow/media/workflow.yaml?ref={SHA}",
    ],
    ids=[
        "raw",
        "a branch",
        "upper case",
        "up",
        "here",
        "no file",
        "more after the version",
        "a contest",
        "a submission",
        "a workflow",
    ],
)
def test_nothing_but_a_tasks_file_at_a_commit_passes_its_map(door: str) -> None:
    assert not _task_map().match(door), door


@pytest.mark.parametrize(
    "asked",
    [
        f"/-/downloads/acme/spring/sum/versions/{SHA}",
        f"/-/downloads/acme/spring/sum/versions/{SHA}/",
        "/-/downloads/acme/spring/sum/versions/main/task.yaml",
        f"/-/downloads/acme/spring/sum/versions/{SHA[:-1]}/task.yaml",
        f"/-/downloads/acme/spring/sum/versions/{SHA.upper()}/task.yaml",
        f"/-/downloads/acme/spring/sum/version/{SHA}/task.yaml",
        f"/-/downloads/acme/spring/versions/{SHA}/task.yaml",
    ],
    ids=[
        "no path",
        "an empty path",
        "a branch",
        "short",
        "upper case",
        "not the word",
        "no task",
    ],
)
def test_nothing_but_a_file_at_a_commit_is_asked_at_the_tasks_door(asked: str) -> None:
    assert not _task_location().match(asked), asked


@pytest.mark.parametrize(
    "path",
    ["workflow.yaml", "scorer/main.py", "tests/a%20b.in", "data/x/y/weights.bin"],
)
def test_every_file_a_workflow_version_holds_is_a_download(path: str) -> None:
    for version in ("v1", "v12"):
        asked = f"/-/downloads/workflows/bob/tuned/versions/{version}/{path}"
        assert _workflow_location().match(asked), asked
    for commit in (SHA, SHA + "0" * 24):
        door = f"/api/v1/repos/bob/tuned.workflow/media/{path}?ref={commit}"
        assert _workflow_map().match(door), door


@pytest.mark.parametrize(
    "door",
    [
        f"/api/v1/repos/bob/tuned.workflow/raw/workflow.yaml?ref={SHA}",
        "/api/v1/repos/bob/tuned.workflow/media/workflow.yaml?ref=main",
        "/api/v1/repos/bob/tuned.workflow/media/workflow.yaml?ref=v1",
        f"/api/v1/repos/bob/tuned.workflow/media/a/../workflow.yaml?ref={SHA}",
        f"/api/v1/repos/bob/tuned.workflow/media?ref={SHA}",
        f"/api/v1/repos/k1/spring.sum.task/media/task.yaml?ref={SHA}",
        f"/api/v1/repos/k1/k2.sub/media/files/a/b?ref={SHA}",
    ],
    ids=[
        "raw",
        "a branch",
        "a version's name",
        "up",
        "no file",
        "a task",
        "a submission",
    ],
)
def test_nothing_but_a_workflows_file_at_a_commit_passes_its_map(door: str) -> None:
    assert not _workflow_map().match(door), door


@pytest.mark.parametrize(
    "asked",
    [
        "/-/downloads/workflows/bob/tuned/versions/v1",
        "/-/downloads/workflows/bob/tuned/versions/v1/",
        "/-/downloads/workflows/bob/tuned/versions/v0/workflow.yaml",
        "/-/downloads/workflows/bob/tuned/versions/v01/workflow.yaml",
        "/-/downloads/workflows/bob/tuned/versions/main/workflow.yaml",
        f"/-/downloads/workflows/bob/tuned/versions/{SHA}/workflow.yaml",
        "/-/downloads/workflow/bob/tuned/versions/v1/workflow.yaml",
        "/-/downloads/workflows/bob/versions/v1/workflow.yaml",
    ],
    ids=[
        "no path",
        "an empty path",
        "v0",
        "a leading zero",
        "a branch",
        "a commit",
        "not the word",
        "no name",
    ],
)
def test_nothing_but_a_file_of_a_named_version_is_asked_at_the_workflows_door(
    asked: str,
) -> None:
    assert not _workflow_location().match(asked), asked


@pytest.mark.parametrize(
    "path",
    ["scorer/main.py", "tests/a%20b.in", "data/x/y/weights.bin", "draft"],
)
def test_every_file_a_workflow_draft_holds_is_a_download(path: str) -> None:
    asked = f"/-/downloads/workflows/bob/tuned/draft/{path}"
    assert _draft_location().match(asked), asked


@pytest.mark.parametrize(
    "asked",
    [
        "/-/downloads/workflows/bob/tuned/draft",
        "/-/downloads/workflows/bob/tuned/draft/",
        "/-/downloads/workflows/bob/tuned/drafts/main.py",
        "/-/downloads/workflows/bob/draft/main.py",
        "/-/downloads/workflow/bob/tuned/draft/main.py",
    ],
    ids=["no path", "an empty path", "not the word", "no name", "not workflows"],
)
def test_nothing_but_a_file_of_a_draft_is_asked_at_the_drafts_door(asked: str) -> None:
    assert not _draft_location().match(asked), asked


def test_neither_kind_of_address_is_taken_by_the_others_location() -> None:
    submitted = [
        "/-/downloads/acme/spring/sum/3/files/submission/main.py",
        f"/-/downloads/acme/spring/sum/3/files/{SHA}/a",
        "/-/downloads/versions/spring/sum/3/files/a/b",
    ]
    task_files = [
        f"/-/downloads/acme/spring/sum/versions/{SHA}/task.yaml",
        f"/-/downloads/acme/spring/sum/versions/{SHA}/files/submission/main.py",
        f"/-/downloads/acme/spring/3/versions/{SHA}/files/a/b",
    ]

    assert all(_location().match(asked) for asked in submitted)
    assert all(_task_location().match(asked) for asked in task_files)
    assert not any(_task_location().match(asked) for asked in submitted)
    assert not any(_location().match(asked) for asked in task_files)

    staff = (
        "/-/downloads/acme/spring/sum/entrants/"
        "00000000-0000-4000-8000-000000000014/1/files/submission/main.py"
    )
    assert _staff_location().match(staff)
    assert not _location().match(staff)
    assert not _task_location().match(staff)
    assert not _staff_location().match(submitted[0])
    assert not _staff_location().match(task_files[0])

    workflow_files = [
        "/-/downloads/workflows/bob/tuned/versions/v1/workflow.yaml",
        "/-/downloads/workflows/bob/tuned/versions/v3/scorer/main.py",
    ]
    # An org may be called `workflows`: its task's files are at a commit.
    org_task = f"/-/downloads/workflows/spring/sum/versions/{SHA}/task.yaml"
    assert all(_workflow_location().match(asked) for asked in workflow_files)
    assert not any(
        location.match(asked)
        for location in (_location(), _staff_location(), _task_location())
        for asked in workflow_files
    )
    assert _task_location().match(org_task)
    assert not _workflow_location().match(org_task)
    assert not any(
        _workflow_location().match(asked) for asked in [*submitted, *task_files, staff]
    )

    draft = "/-/downloads/workflows/bob/tuned/draft/scorer/main.py"
    assert not any(
        location.match(draft)
        for location in (
            _location(),
            _staff_location(),
            _task_location(),
            _workflow_location(),
        )
    )
    assert not any(
        _draft_location().match(asked)
        for asked in [*submitted, *task_files, staff, *workflow_files, org_task]
    )


def _location_body(pattern: str) -> str:
    start = CONFIG.index(f'location ~ "{pattern}" {{')
    return CONFIG[start : CONFIG.index("\n    }\n", start)]


def _directives(text: str) -> set[str]:
    """Each directive's name, comments and the `if` block's own lines left
    out, as a way to tell what a block sets."""
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and line[0].isalpha():
            names.add(line.split()[0])
    return names


@pytest.mark.parametrize(
    "pattern",
    _locations(),
    ids=["submission", "staff file", "task file", "workflow file", "draft file"],
)
def test_every_download_location_shares_one_file_of_directives(pattern: str) -> None:
    body = _location_body(pattern)
    own = _directives(body)

    assert f"include {SHARED_AT};" in body
    assert own == {
        "location",
        "auth_request_set",
        "proxy_set_header",
        "include",
        "proxy_pass",
    }
    assert re.findall(r"^\s*proxy_set_header (\S+)", body, re.M) == ["Host"]
    assert len(re.findall(r"^\s*auth_request_set ", body, re.M)) == 1


def test_the_shared_file_says_what_a_download_is() -> None:
    for header in (
        "add_header Content-Type application/octet-stream always;",
        "add_header Content-Disposition $download_disposition always;",
        'add_header Cache-Control "private, no-store" always;',
        "add_header X-Content-Type-Options nosniff always;",
        "add_header Content-Security-Policy \"default-src 'none'; sandbox\" always;",
        "auth_request /-/downloads/door;",
        "= @download_refused;",
    ):
        assert header in SHARED, header


def test_no_header_a_download_sets_is_also_passed_on_from_the_forge() -> None:
    """Forgejo's media endpoint sends its own `X-Content-Type-Options`, and
    names the object and the headers its own pages read; a header the door
    sets would otherwise reach the browser twice, and the rest are Forgejo's
    and not this site's."""
    hidden = {
        name.lower() for name in re.findall(r"^proxy_hide_header (\S+);", SHARED, re.M)
    }
    added = {name.lower() for name in re.findall(r"^add_header (\S+) ", SHARED, re.M)}

    assert added <= hidden, added - hidden
    assert {
        "set-cookie",
        "access-control-expose-headers",
        "x-forgejo-object-type",
        "x-gitea-object-type",
    } <= hidden


def test_compose_mounts_the_shared_file_where_the_site_includes_it() -> None:
    compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))

    assert (
        f"./proxy/download.conf:{SHARED_AT}:ro"
        in compose["services"]["proxy"]["volumes"]
    )
