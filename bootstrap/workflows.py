"""The built-in workflow the forge is seeded with, unicon/classic@v1.

A task's first save reads the workflow it names through the forge, as the
saving organiser, so a workflow that organiser can read has to exist before
any task can be published. The forge package finds a workflow by its shape
and not by a registry: a repository `<owner>/<name>.workflow` carrying the
topic `unicon-workflow`, with a git tag for each version. The built-in ones
live in the platform org `unicon`, which is `limited` like every org, so a
signed-in organiser from another org can read its public repositories, and
whose only owner is the platform account, because bootstrap makes it with
that account's token.

The repository gets its two files, workflow.yaml and README.md, in one commit,
and the tag v1 points at that commit. They are read from workflows/classic/
in this repo, so the definition is a file a person can read and yamllint
checks, not a string in this module. The primitives the definition names and
the image digests behind them arrive with the first grading (feature 6);
until then a save can read the workflow and nothing can run it.

Every piece checks before it makes anything: the organisation, the
repository, the first commit, the mark and the version each report present on
a rerun. A repository that already has a commit is left as it is, even when
the files here have changed since. The tag v1 is a frozen version, and a
change to the definition is a later version, never a rewrite of this one.

Every call goes through the Forgejo API from inside its container, with the
provisioning token, the way forgejo.py reaches it. Anything the API answers
404 for is absent; any other refusal stops the run.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from bootstrap.forgejo import Forgejo, ForgejoError

PLATFORM_ORG = "unicon"
PLATFORM_ORG_DESCRIPTION = "Unicon's own workflows and primitives"
WORKFLOW_TOPIC = "unicon-workflow"
DEFAULT_BRANCH = "main"

CLASSIC = "classic"
CLASSIC_REPO = f"{CLASSIC}.workflow"
CLASSIC_VERSION = "v1"
SEED_FILES = ("workflow.yaml", "README.md")


def seed_files(directory: Path, name: str) -> dict[str, bytes]:
    """The files the workflow `name` is seeded with, from workflows/<name>/
    under the deploy directory.
    """
    root = directory / "workflows" / name
    return {path: (root / path).read_bytes() for path in SEED_FILES}


class Workflows:
    def __init__(self, forgejo: Forgejo, token: str) -> None:
        self._forgejo = forgejo
        self._token = token

    def ensure_platform_org(self) -> bool:
        """Returns True if this run created the platform org.

        Made with the provisioning token, so its Owners team holds the
        platform account and nobody else. Limited, not private: Forgejo hides
        every repository of a private organisation from non-members, public
        ones included, and the built-in workflows are there to be read from
        every org.
        """
        if self._found("GET", f"/orgs/{PLATFORM_ORG}") is not None:
            return False
        _ok(
            self._call(
                "POST",
                "/orgs",
                body={
                    "username": PLATFORM_ORG,
                    "description": PLATFORM_ORG_DESCRIPTION,
                    "visibility": "limited",
                    "repo_admin_change_team_access": False,
                },
            )
        )
        return True

    def ensure_repository(self, org: str, repo: str) -> bool:
        """Returns True if this run created the repository: public, with the
        default branch every write of the forge package goes to, and no
        first commit of Forgejo's own so that the first commit is the files.
        """
        if self._found("GET", f"/repos/{org}/{repo}") is not None:
            return False
        _ok(
            self._call(
                "POST",
                f"/orgs/{org}/repos",
                body={
                    "name": repo,
                    "private": False,
                    "default_branch": DEFAULT_BRANCH,
                    "auto_init": False,
                },
            )
        )
        return True

    def ensure_first_commit(
        self, org: str, repo: str, files: dict[str, bytes], message: str
    ) -> tuple[str, bool]:
        """Returns the head commit of the default branch and whether this run
        made it. A repository with a commit already is left alone.
        """
        branch = self._found("GET", f"/repos/{org}/{repo}/branches/{DEFAULT_BRANCH}")
        if branch is not None:
            return str(branch["commit"]["id"]), False
        written = _ok(
            self._call(
                "POST",
                f"/repos/{org}/{repo}/contents",
                body={
                    "branch": DEFAULT_BRANCH,
                    "new_branch": DEFAULT_BRANCH,
                    "message": message,
                    "files": [
                        {
                            "operation": "create",
                            "path": path,
                            "content": base64.b64encode(content).decode(),
                        }
                        for path, content in sorted(files.items())
                    ],
                },
            )
        )
        return str(written["commit"]["sha"]), True

    def ensure_mark(self, org: str, repo: str, topic: str) -> bool:
        """Returns True if this run put the topic on the repository."""
        listed = _ok(self._call("GET", f"/repos/{org}/{repo}/topics"))
        if topic in (listed.get("topics") or []):
            return False
        _ok(self._call("PUT", f"/repos/{org}/{repo}/topics/{topic}"))
        return True

    def ensure_version(self, org: str, repo: str, version: str, target: str) -> bool:
        """Returns True if this run made the tag. An existing tag is kept
        where it points, whatever `target` says: a version is frozen.
        """
        if self._found("GET", f"/repos/{org}/{repo}/tags/{version}") is not None:
            return False
        _ok(
            self._call(
                "POST",
                f"/repos/{org}/{repo}/tags",
                body={"tag_name": version, "target": target},
            )
        )
        return True

    def _found(self, method: str, path: str) -> Any | None:
        """The body of an answer, or None when Forgejo has no such thing."""
        status, body = self._call(method, path)
        if status == 404:
            return None
        return _ok((status, body))

    def _call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return self._forgejo.api(method, path, token=self._token, body=body)


def _ok(answer: tuple[int, Any]) -> Any:
    status, body = answer
    if status >= 400:
        raise ForgejoError(f"Forgejo answered {status}: {str(body)[:200]}")
    return body
