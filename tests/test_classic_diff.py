"""The diff script unicon/classic@v5 ships, workflows/classic/v5/diff/:
words compared as testlib's wcmp compares them, every case of
unicon/diff-check@v2's tests judged by that rule, the report it writes
for exec, and the workflow beside it that runs it on the solution's output.

Its run through exec, in manifest mode on the exec image, is in
primitive-exec's image tests, which read this folder from a deploy checkout
beside them.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
from types import ModuleType

import pytest

from bootstrap import core_yaml

DIFF = Path(__file__).resolve().parent.parent / "workflows/classic/v5/diff"


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
    (b" 1 2 3\n", b"1 2 3\n"),
    (b"1  2 3\n", b"1 2 3\n"),
    (b"a\n\nb\n", b"a\nb\n"),
    (b"\n\nyes\n", b"yes\n"),
]
"""diff-check@v2's cases that match by words: the first eight matched there
too; leading whitespace, inner spacing and blank lines between lines, which
counted there, count no more."""

DIFFERING = [
    (b"1 2 3\n", b"1 2 4\n"),
    (b"a\n", b"a\nb\n"),
    (b"", b"0\n"),
]
"""diff-check@v2's cases that still differ: a different word, one more
word, and a word against nothing."""


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
    ("actual", "expected"),
    [
        (b"1\n2\n3\n", b"1 2 3\n"),
        (b"1\n2\n3", b"1 2 3"),
        (b"3\n1 4 1\n5 9\n", b"3 1 4\n1 5 9\n"),
        (b"1\t2\r\n\r\n3", b"1 2 3"),
        (b"  42", b"42"),
    ],
)
def test_an_answer_split_across_lines_or_spaced_otherwise_matches(
    tmp_path: Path, actual: bytes, expected: bytes
) -> None:
    left = write(tmp_path / "output", actual)
    right = write(tmp_path / "answer", expected)
    assert diff.same(left, right)
    assert diff.same(right, left)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (b"yes\n\n\n\n", b"yes"),
        (b"yes", b"yes\n\n  \n\t\n"),
        (b"1 2\n\r\n\n", b"1 2\n"),
        (b"\n \n", b""),
    ],
)
def test_trailing_blank_lines_do_not_count(
    tmp_path: Path, actual: bytes, expected: bytes
) -> None:
    left = write(tmp_path / "output", actual)
    right = write(tmp_path / "answer", expected)
    assert diff.same(left, right)
    assert diff.same(right, left)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (b"12 3\n", b"1 23\n"),
        (b"1 2\n", b"12\n"),
        (b"Yes\n", b"yes\n"),
        (b"1.0\n", b"1\n"),
        (b"1 2 3\n", b"1 2 3 4\n"),
        (b"a\x0bb\n", b"a b\n"),
        (b"a\x0cb\n", b"a\nb\n"),
    ],
)
def test_words_that_differ_or_are_split_differently_do_not_match(
    tmp_path: Path, actual: bytes, expected: bytes
) -> None:
    """A vertical tab or a form feed is part of a word, as in testlib."""
    left = write(tmp_path / "output", actual)
    right = write(tmp_path / "answer", expected)
    assert not diff.same(left, right)
    assert not diff.same(right, left)


@pytest.mark.parametrize(
    ("actual", "expected", "matches"),
    [
        (b"ab  ", b"ab\n", True),
        (b"ab  \t\n\n", b"ab", True),
        (b"ab  c", b"ab c", True),
        (b"abc d", b"ab cd", False),
        (b"abcdef", b"abc def", False),
        (b"abcdef", b"abcdef\n", True),
        (b"abcdefg", b"abcdef", False),
        (b"a b", b"a\n\n\nb\n\n", True),
        (b"  \n  ab", b"ab", True),
        (b"x\x0by z", b"x\x0by\nz", True),
    ],
)
def test_words_across_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actual: bytes,
    expected: bytes,
    matches: bool,
) -> None:
    """Words split over the blocks the files are read in are judged as if
    read whole."""
    monkeypatch.setattr(diff, "BLOCK", 2)
    left = write(tmp_path / "output", actual)
    right = write(tmp_path / "answer", expected)
    assert diff.same(left, right) is matches
    assert diff.same(right, left) is matches


@pytest.mark.parametrize("block", [1, 2, 3, 5, 64])
@pytest.mark.parametrize(
    "data",
    [
        b"",
        b" ",
        b"word",
        b"two words",
        b"  lead and trail  \n\n",
        b"a\tb\rc\nd  e\r\n\r\nf",
        b"a\x0bb \x0cc\x0b \x0c",
        b"longer words split over many blocks here",
    ],
)
def test_a_file_reads_as_its_words_one_space_apart(
    monkeypatch: pytest.MonkeyPatch, data: bytes, block: int
) -> None:
    """However the file falls into blocks, it reads as the whole file's
    words, split on spaces, tabs, carriage returns and line feeds, one space
    apart."""
    monkeypatch.setattr(diff, "BLOCK", block)
    found = list(diff.spaced(io.BytesIO(data)))
    assert all(found)
    words = data
    for space in b"\t\r\n":
        words = words.replace(bytes((space,)), b" ")
    assert b"".join(found) == b" ".join(word for word in words.split(b" ") if word)


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


def test_the_workflow_runs_the_solution_then_the_diff_script_on_its_output() -> None:
    workflow = core_yaml.load((DIFF.parent / "workflow.yaml").read_bytes())
    assert "test" not in workflow
    steps = {step["id"]: step for step in workflow["steps"]}
    assert list(steps) == ["solution", "compare"]
    assert {step["use"] for step in steps.values()} == {"unicon/exec@v3"}
    assert steps["solution"]["with"]["source"] == "${{ inputs.code }}"
    assert steps["solution"]["with"]["language"] == "${{ inputs.language }}"
    compare = steps["compare"]["with"]
    assert compare["source"] == "${{ workflow.diff/ }}"
    assert compare["language"] == "python"
    assert compare["manifest"] == "${{ workflow.diff/program.yaml }}"
    assert (compare["time_limit"], compare["memory_limit"]) == (3, 256)
    assert set(compare["files"]) == {"output", "answer"}
    assert "self_reported" not in compare
    assert workflow["outputs"]["score"] == "${{ steps.compare.score }}"
    assert workflow["outputs"]["log"]["from"] == "${{ steps.solution.log }}"
