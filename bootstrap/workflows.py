"""The built-in workflow the forge is seeded with, unicon/classic, at each of
its versions.

A task's first save reads the workflow it names through the forge, as the
saving organiser, so a workflow that organiser can read has to exist before
any task can be published. It is the public repository
`unicon/classic.workflow` in the platform org, carrying the topic
`unicon-workflow`, with a tag per version (platform_repos.py).

Each version is a folder in this repo, workflows/classic/v1/,
workflows/classic/v2/ and on, holding the two files the version's tag holds,
workflow.yaml and README.md, so the definition is a file a person can read
and yamllint checks, not a string in this module. The definition wires the
three primitives together: compile the submission, run it on every test under
the task's limits, and compare each output with that test's answer. v1 wires
the primitives' v1 and v2 their v2. Every primitive version a definition
`use:`s has to be one the image manifest pins, or bootstrap refuses before it
writes anything, since the forge would refuse every task saved against it.

Bootstrap publishes every version folder to its own tag. A version is frozen:
a tag already at the forge is left as it is, and one whose folder here has
changed since stops the run before anything is seeded, naming it; a changed
definition is a new folder. The one exception is a development stack, where
bootstrap rewrites each version in place when it is run with --rewrite.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW_TOPIC = "unicon-workflow"

CLASSIC = "classic"
SEED_FILES = ("workflow.yaml", "README.md")
DEFINITION = "workflow.yaml"

_VERSION = re.compile(r"^v([1-9][0-9]*)$")
_USE = re.compile(r"(?:^|[\s{,])use\s*:\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s,}]+))")


class WorkflowError(ValueError):
    """The seed folders of a workflow do not have the shape above."""


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
