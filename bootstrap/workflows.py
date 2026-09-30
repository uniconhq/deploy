"""The built-in workflow the forge is seeded with, unicon/classic@v1.

A task's first save reads the workflow it names through the forge, as the
saving organiser, so a workflow that organiser can read has to exist before
any task can be published. It is the public repository
`unicon/classic.workflow` in the platform org, carrying the topic
`unicon-workflow`, with the tag v1 (platform_repos.py).

The repository holds two files, workflow.yaml and README.md, read from
workflows/classic/ in this repo, so the definition is a file a person can read
and yamllint checks, not a string in this module. The definition wires the
three primitives together: compile the submission, run it on every testcase
under the task's limits, and compare each output with that testcase's answer.

A workflow's versions are made deliberately. A rerun leaves v1 as it is even
when the files here have changed since, and says so; a changed definition is a
later version. The one exception is a development stack, where bootstrap
rewrites v1 in place when it is run with --rewrite-v1.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW_TOPIC = "unicon-workflow"

CLASSIC = "classic"
CLASSIC_REPO = f"{CLASSIC}.workflow"
SEED_FILES = ("workflow.yaml", "README.md")


def seed_files(directory: Path, name: str) -> dict[str, bytes]:
    """The files the workflow `name` is seeded with, from workflows/<name>/
    under the deploy directory.
    """
    root = directory / "workflows" / name
    return {path: (root / path).read_bytes() for path in SEED_FILES}
