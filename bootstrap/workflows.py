"""The built-in workflows the forge is seeded with, each at every one of its
versions: unicon/classic, and unicon/classic-folder, classic over a folder of
sources with an entry point.

A task's first save reads the workflow it names through the forge, as the
saving organiser, so a workflow that organiser can read has to exist before
any task can be published. Each is a public repository
`unicon/<name>.workflow` in the platform org, carrying the topic
`unicon-workflow`, with a tag per version (platform_repos.py).

Each workflow is a folder under workflows/ in this repo, and each version a
folder in it, workflows/classic/v2/ and on, holding
the two files the version's tag holds, workflow.yaml and README.md, so the
definition is a file a person can read and yamllint checks, not a string in
this module. classic wires the three primitives together: compile the
submission, run it on every test under the task's limits, and compare each
output with that test's answer, over the primitives' v2.
classic-folder does the same with compile@v2 over a folder and its entry.
Every primitive version a definition `use:`s has to be one the image
manifest pins, or bootstrap refuses before it writes anything, since the
forge would refuse every task saved against it.

Bootstrap publishes every version folder to its own tag. A version is frozen
in what grades: a tag already at the forge is left as it is, and one whose
definition here has changed since in anything the compiler reads stops the
run before anything is seeded, naming it; such a change is a new folder. A
change only to what the definition tells the task's form (an input's
`title`, `help` and `unit`), to its comments or layout, or to the README
changes nothing that grades, so it moves the tag to the new files instead.
The one exception is a development stack, where bootstrap rewrites each
version in place whatever differs when it is run with --rewrite.
"""

from __future__ import annotations

import re
from pathlib import Path

from bootstrap import core_yaml
from bootstrap.platform_repos import PLATFORM_ORG, PlatformRepos

WORKFLOW_TOPIC = "unicon-workflow"

CLASSIC = "classic"
SEED_FILES = ("workflow.yaml", "README.md")
DEFINITION = "workflow.yaml"

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
    """The files version `version` of the workflow `name` is seeded with."""
    root = directory / "workflows" / name / version
    return {path: (root / path).read_bytes() for path in SEED_FILES}


FORM_TEXT = ("title", "help", "unit")
"""What an input tells the task's form and nothing else."""


def moves_in_place(
    repos: PlatformRepos, name: str, version: str, files: dict[str, bytes]
) -> bool:
    """Whether the version at the forge holds other files than these that
    grade alike: the same definition but for its inputs' form text, its
    comments or its layout, whatever the README says. Read without writing
    anything.
    """
    repo = repository(name)
    commit = repos.versions(PLATFORM_ORG, repo).get(version)
    if commit is None or repos.holds(PLATFORM_ORG, repo, commit, files, None):
        return False
    present = repos.read_file(PLATFORM_ORG, repo, DEFINITION, commit)
    if present is None:
        return False
    read = graded(present)
    return read is not None and read == graded(files[DEFINITION])


def graded(definition: bytes) -> object | None:
    """What grades of a definition: the document as the forge parses it,
    without any input's form text, so a comment, the layout and that text
    change nothing. The forge reads a declaration given as a bare type,
    `time_limit: number`, as `{type: number}`, and a flag left out as
    false, so both are read that way here. None when it does not parse as a
    mapping.
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
