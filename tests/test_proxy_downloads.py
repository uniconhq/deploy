"""The download door's two patterns in the proxy's config, read from the file
as nginx reads them: the location a download is asked at, and the map that
lets through only the forge's own answer for it. A submission's files sit
under `files/<input>/`, a folder input's in folders of their own and a
per-test input's as `<group>/<test>`, and the forge names each at the
submission's commit, so both patterns take every such path and nothing
else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONFIG = (Path(__file__).resolve().parent.parent / "proxy" / "nginx.conf").read_text()


def _location() -> re.Pattern[str]:
    found = re.search(r'location ~ "(\^/-/downloads/[^"]+)"', CONFIG)
    assert found is not None
    return re.compile(found.group(1))


def _map() -> re.Pattern[str]:
    block = CONFIG[CONFIG.index("map $download_path $download_target") :]
    found = re.search(r'"~(\^/api/v1/repos/[^"]+)"', block)
    assert found is not None
    return re.compile(found.group(1))


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
