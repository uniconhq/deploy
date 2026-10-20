"""The diff script unicon/classic@v3 ships, workflows-next/classic/v3/diff/:
the rule of unicon/diff-check@v2, every case its tests hold, and the report
it writes for exec.

Its run through exec, in manifest mode on the exec image, is in
primitive-exec's image tests, which read this folder from a deploy checkout
beside them.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

from bootstrap import core_yaml

DIFF = Path(__file__).resolve().parent.parent / "workflows-next/classic/v3/diff"


def load_diff() -> ModuleType:
    spec = importlib.util.spec_from_file_location("diff", DIFF / "diff.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diff = load_diff()


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


MATCHING = [
    (b"1 2 3\n", b"1 2 3\n"),
    (b"1 2 3", b"1 2 3\n"),
    (b"1 2 3   \t\n", b"1 2 3\n"),
    (b"1 2 3\r\n4\r\n", b"1 2 3\n4\n"),
    (b"yes\n\n\n", b"yes\n"),
    (b"yes\n", b"yes\n\n  \n"),
    (b"", b"\n\n"),
    (b"", b""),
]
"""diff-check@v2's matching cases: trailing whitespace on a line and
trailing blank lines do not count."""

DIFFERING = [
    (b"1 2 3\n", b"1 2 4\n"),
    (b" 1 2 3\n", b"1 2 3\n"),
    (b"1  2 3\n", b"1 2 3\n"),
    (b"a\n\nb\n", b"a\nb\n"),
    (b"a\n", b"a\nb\n"),
    (b"", b"0\n"),
    (b"\n\nyes\n", b"yes\n"),
]
"""diff-check@v2's differing cases: leading whitespace, inner spacing, inner
blank lines and content all count."""


@pytest.mark.parametrize(("actual", "expected"), MATCHING)
def test_matching_files_are_the_same(
    tmp_path: Path, actual: bytes, expected: bytes
) -> None:
    left = write(tmp_path / "output", actual)
    right = write(tmp_path / "answer", expected)
    assert diff.same(left, right)
    assert diff.same(right, left)


@pytest.mark.parametrize(("actual", "expected"), DIFFERING)
def test_differing_files_are_not_the_same(
    tmp_path: Path, actual: bytes, expected: bytes
) -> None:
    left = write(tmp_path / "output", actual)
    right = write(tmp_path / "answer", expected)
    assert not diff.same(left, right)
    assert not diff.same(right, left)


@pytest.mark.parametrize(
    ("actual", "expected", "matches"),
    [
        (b"ab  ", b"ab\n", True),
        (b"ab  \t\n\n", b"ab", True),
        (b"ab  c", b"ab c", False),
        (b"ab  c", b"ab  c  \n", True),
        (b"  \n  ab", b"\nab", False),
        (b"  \n  ab", b"\n  ab", True),
        (b"1 2 \n3", b"1 2\n3", True),
        (b"1 2 \n3", b"1 23", False),
        (b"xy \t\r\x0b\x0c \nz\n", b"xy\nz", True),
    ],
)
def test_whitespace_across_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actual: bytes,
    expected: bytes,
    matches: bool,
) -> None:
    """Whitespace split over the blocks the files are read in is judged as
    if read whole."""
    monkeypatch.setattr(diff, "BLOCK", 2)
    left = write(tmp_path / "output", actual)
    right = write(tmp_path / "answer", expected)
    assert diff.same(left, right) is matches
    assert diff.same(right, left) is matches


@pytest.mark.parametrize(
    ("data", "lines"),
    [
        (b"a\r\nb \n c\t\n", b"a\nb\n c\n"),
        (b"a \r\n\x0c\n", b"a\n\n"),
        (b"a \t\r\x0b\x0c \t\nb  c   \n", b"a\nb  c\n"),
        (b"a b", b"a b"),
    ],
)
def test_trim_lines(data: bytes, lines: bytes) -> None:
    assert diff.trim_lines(data) == lines


def report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes, answer: bytes
) -> dict[str, str]:
    """Run the script as exec does, in a folder holding the two files, and
    return what it reported, by file."""
    work = tmp_path / "work"
    folder = tmp_path / "report"
    work.mkdir()
    folder.mkdir()
    write(work / "output", output)
    write(work / "answer", answer)
    monkeypatch.chdir(work)
    monkeypatch.setenv("UNICON_REPORT", str(folder))
    assert diff.main() == 0
    return {path.name: path.read_text() for path in folder.iterdir()}


def test_a_match_reports_verdict_0_and_a_score_of_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert report(tmp_path, monkeypatch, b"42 \n", b"42\n\n") == {
        "verdict": "0\n",
        "score": "1\n",
    }


def test_a_mismatch_reports_verdict_1_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its score counts as 0, the worst bound, without being written."""
    assert report(tmp_path, monkeypatch, b"41\n", b"42\n") == {"verdict": "1\n"}


def test_a_missing_answer_ends_the_script_with_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A program that cannot judge fails, which exec makes a fault."""
    folder = tmp_path / "report"
    folder.mkdir()
    write(tmp_path / "output", b"42\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UNICON_REPORT", str(folder))
    with pytest.raises(FileNotFoundError):
        diff.main()
    assert os.listdir(folder) == []


def test_the_manifest_declares_the_score_with_its_bounds() -> None:
    """program.yaml, as the save reads it and as exec gets it in JSON."""
    manifest = core_yaml.load((DIFF / "program.yaml").read_bytes())
    as_json = json.loads(json.dumps(manifest, default=int))
    assert as_json == {
        "reports": {"score": {"better": "higher", "at_least": 0, "at_most": 1}}
    }
