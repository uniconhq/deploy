"""The download door's patterns in the proxy's config, read from the file as
nginx reads them: for each of its two kinds of address, the location a
download is asked at, and the map that lets through only the forge's own
answer for it. A submission's files sit under `files/<input>/`, a folder
input's in folders of their own and a per-test input's as `<group>/<test>`,
and the forge names each at the submission's commit, so both patterns take
every such path and nothing else. A task's own files are asked for at
`versions/<commit>/<path>`, any file of the task at any depth, and the forge
names each in the task's own repository at that commit. Neither kind's
patterns take the other's addresses.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONFIG = (Path(__file__).resolve().parent.parent / "proxy" / "nginx.conf").read_text()


def _locations() -> list[str]:
    return re.findall(r'location ~ "(\^/-/downloads/[^"]+)"', CONFIG)


def _location() -> re.Pattern[str]:
    """The submission's location, the one whose fourth part is a number."""
    found = [pattern for pattern in _locations() if "/versions/" not in pattern]
    assert len(found) == 1, found
    return re.compile(found[0])


def _task_location() -> re.Pattern[str]:
    found = [pattern for pattern in _locations() if "/versions/" in pattern]
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


SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize(
    "path",
    [
        "files/submission/main.py",
        "files/out/main/1.txt",
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
        "tests/main/1/answer",
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
