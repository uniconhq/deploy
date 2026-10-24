"""The built-in workflows the forge is seeded with, each at every one of its
versions: unicon/classic, unicon/classic-folder, classic over a folder of
sources with an entry point, and unicon/interactive, an entrant's program
against the task's judge.

A task's first save reads the workflow it names through the forge, as the
saving organiser, so a workflow that organiser can read has to exist before
any task can be published. Each is a public repository
`unicon/<name>.workflow` in the platform org, carrying the topic
`unicon-workflow`, with a tag per version (platform_repos.py).

Each workflow is a folder under workflows/ in this repo, and each version a
folder in it, workflows/classic/v2/ and on, holding every file the
version's tag holds: workflow.yaml and its README.md, and the files its
steps read from the workflow itself, such as classic@v3's diff script under
diff/ and interactive@v1's verdict script under verdict/. So the definition
is a file a person can read and yamllint checks, not a string in this
module. Python's caches are left out, and so, in a git checkout, is every
file git does not track, such as a `.DS_Store`; a link is refused, since
the forge stages a folder given whole with everything in it. classic@v2 wires
the three primitives together over the task's tests; classic@v3 is one run
of the entrant's program, compiled, run with exec and compared by its diff
script; classic-folder does what classic@v2 does with compile@v2 over a
folder and its entry. Every primitive version a definition `use:`s has to
be one the image manifest pins, or bootstrap refuses before it writes
anything, since the forge would refuse every task saved against it.

Bootstrap publishes every version folder to its own tag. A version is frozen
in what grades: a tag already at the forge is left as it is, and one whose
files here have changed since in anything that grades stops the run before
anything is seeded, naming it; such a change is a new folder. A change only
to what the definition tells a person (an input's `title`, `help` and
`unit`, a report's `title` and `unit`), to its comments or layout, or to the
README changes nothing that grades, so it moves the tag to the new files
instead. Any other file, a script a step runs, grades as it is.
The one exception is a development stack, where bootstrap rewrites each
version in place whatever differs when it is run with --rewrite.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bootstrap import core_yaml
from bootstrap.platform_repos import PLATFORM_ORG, PlatformRepos

WORKFLOW_TOPIC = "unicon-workflow"

CLASSIC = "classic"
DEFINITION = "workflow.yaml"
README = "README.md"
LEFT_OUT = ("__pycache__", ".DS_Store")
"""Names under a version that are never seeded: Python's caches, which
running the tests of a version's scripts leaves beside them, and the
folder settings macOS's Finder leaves."""

_VERSION = re.compile(r"^v([1-9][0-9]*)$")
_USE = re.compile(r"(?:^|[\s{,])use\s*:\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s,}]+))")


class WorkflowError(ValueError):
    """The seed folders of a workflow do not have the shape above."""


_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def seeded(directory: Path) -> list[str]:
    """The name of every built-in workflow, each folder under workflows/ in
    the deploy directory, in name order.
    """
    root = directory / "workflows"
    names: list[str] = []
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        if not entry.is_dir() or _NAME.match(entry.name) is None:
            raise WorkflowError(
                f"{entry} is not a workflow folder; workflows/ holds one folder "
                "per built-in workflow, named as the workflow is"
            )
        names.append(entry.name)
    if not names:
        raise WorkflowError(f"{root} holds no workflow folder")
    return names


def repository(name: str) -> str:
    return f"{name}.workflow"


def seed_versions(directory: Path, name: str) -> dict[str, dict[str, bytes]]:
    """Every version the workflow `name` is seeded with, oldest first: each
    folder workflows/<name>/v<N>/ under the deploy directory, as its files.
    """
    root = directory / "workflows" / name
    numbered: list[tuple[int, str]] = []
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        match = _VERSION.match(entry.name)
        if not entry.is_dir() or match is None:
            raise WorkflowError(
                f"{entry} is not a version folder; workflows/{name}/ holds one "
                "folder per version, v1, v2 and on"
            )
        numbered.append((int(match.group(1)), entry.name))
    if not numbered:
        raise WorkflowError(f"{root} holds no version folder")
    return {
        version: seed_files(directory, name, version) for _, version in sorted(numbered)
    }


def seed_files(directory: Path, name: str, version: str) -> dict[str, bytes]:
    """The files version `version` of the workflow `name` is seeded with:
    every file in its folder, by its path there, but Python's caches, the
    Finder's .DS_Store and, in a git checkout, any file git ignores. A file
    not yet added to git is seeded, as it would be once added. It must hold
    workflow.yaml and README.md; a link is refused.
    """
    root = directory / "workflows" / name / version
    ignored = _ignored(root)
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in LEFT_OUT or part.endswith(".pyc") for part in relative.parts):
            continue
        if relative.as_posix() in ignored and path.is_file():
            continue
        if path.is_symlink():
            raise WorkflowError(
                f"{path} is a link; a workflow version holds files and folders "
                "only, since the forge stages a folder given whole as it is"
            )
        if path.is_file():
            files[relative.as_posix()] = path.read_bytes()
    for needed in (DEFINITION, README):
        if needed not in files:
            raise WorkflowError(f"{root} holds no {needed}")
    return files


def _ignored(root: Path) -> set[str]:
    """The files under `root` git ignores, by their path there; none when
    `root` is not in a git checkout."""
    try:
        listed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--",
                ".",
            ],
            capture_output=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return set()
    return {name for name in listed.stdout.decode().split("\0") if name}


FORM_TEXT = ("title", "help", "unit")
"""What an input tells the task's form and nothing else."""

VALUE_TEXT = ("title", "unit")
"""What a reported value tells the pages that name it and nothing else."""

TEXT_FILES = (DEFINITION, README)
"""The files of a version that may change without a new version, the
definition in its words and the README; any other grades as it is."""


def moves_in_place(
    repos: PlatformRepos, name: str, version: str, files: dict[str, bytes]
) -> bool:
    """Whether the version at the forge holds other files than these that
    grade alike: the same definition but for its inputs' form text, its
    outputs' titles and units, its comments or its layout, whatever the
    README says, and every other file the same. Read without writing
    anything.
    """
    repo = repository(name)
    commit = repos.versions(PLATFORM_ORG, repo).get(version)
    if commit is None or repos.holds(PLATFORM_ORG, repo, commit, files, None):
        return False
    others = tuple(path for path in files if path not in TEXT_FILES)
    if set(repos.blobs(PLATFORM_ORG, repo, commit)) != set(files) or not repos.holds(
        PLATFORM_ORG, repo, commit, files, others
    ):
        return False
    present = repos.read_file(PLATFORM_ORG, repo, DEFINITION, commit)
    if present is None:
        return False
    read = graded(present)
    return read is not None and read == graded(files[DEFINITION])


def graded(definition: bytes) -> object | None:
    """What grades of a definition: the document as the forge parses it,
    without any input's form text or any output's title and unit, so a
    comment, the layout and that text change nothing. The forge reads a
    declaration given as a bare type, `time_limit: number`, as `{type:
    number}`, a flag left out as false, and an output given as a bare
    reference as one given `from` alone, so each is read that way here,
    under `outputs` in a run workflow and `report` in one with a `test`
    block. A `reports` key is read the same way, so a file from before the
    rename still compares. None when it does not parse as a mapping.
    """
    try:
        document = core_yaml.load(definition)
    except core_yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    for section, dropped in (("inputs", FORM_TEXT), ("test", ())):
        declarations = document.get(section)
        if isinstance(declarations, dict):
            document[section] = {
                key: _long_form(value, dropped) for key, value in declarations.items()
            }
    steps = document.get("steps")
    if isinstance(steps, list):
        document["steps"] = [_without(step, ()) for step in steps]
    for section in ("outputs", "reports", "report"):
        reports = document.get(section)
        if isinstance(reports, dict):
            document[section] = {
                name: _without(
                    {"from": entry} if isinstance(entry, str) else entry, VALUE_TEXT
                )
                for name, entry in reports.items()
            }
    return document


def _long_form(declared: object, dropped: tuple[str, ...]) -> object:
    """A declaration as the forge reads it: a bare type name is `{type:
    <name>}`, and a flag set false is the same as one left out; the parts
    `dropped` are left out.
    """
    return _without(
        {"type": declared} if isinstance(declared, str) else declared, dropped
    )


def _without(declared: object, dropped: tuple[str, ...]) -> object:
    """A mapping without the parts `dropped` and without a flag set false,
    which is the same as one left out; anything else as it is.
    """
    if not isinstance(declared, dict):
        return declared
    return {
        part: held
        for part, held in declared.items()
        if part not in dropped and held is not False
    }


def primitives_used(definition: bytes) -> list[str]:
    """Every reference a definition's steps `use:`, in the order written, as
    `owner/name@version`. Read from the lines rather than parsed, the way
    bootstrap reads `primitive.yaml`; a comment is not read.
    """
    used: list[str] = []
    for line in definition.decode("utf-8").splitlines():
        text = line.split(" #", 1)[0]
        if text.lstrip().startswith("#"):
            continue
        for match in _USE.finditer(text):
            used.append(next(group for group in match.groups() if group is not None))
    return used
