"""The platform org `unicon` and the versioned repositories bootstrap keeps in it.

The built-in workflows and the primitives live in the platform org, which is
`limited` like every org, so a signed-in organiser from another org can read
its public repositories, and whose only owner is the platform account, because
bootstrap makes it with that account's token. The forge package finds each of
them by its shape and not by a registry: a public repository named
`<name>.workflow` or `<name>.primitive`, carrying its mark as a topic, with a
git tag for each version, `v1`, `v2` and on.

A version is frozen. `publish` makes one named version out of a set of files:
the default branch is brought to exactly those files in one commit, and the
version's tag points at that commit. A tag already there is never moved: when
it holds the same files the run makes nothing, so bootstrap can run as often
as anyone likes, and when it holds others the version differs, which
`standing` reads without writing anything so that a run can stop before it
seeds. The comparison is by git blob id, computed here from the bytes and
read back from the forge's tree listing, so nothing is downloaded to decide
that nothing changed.

Rewriting a version in place is for a development stack only, and only when
the person running bootstrap asks for it: the default branch gets a new commit
and the tag is moved to it. Anything published against the old commit then
names files that are no longer at that tag, which is exactly why it is never
done without being told to.

Every call goes through the Forgejo API from inside its container, with the
admin token, the way forgejo.py reaches it. Anything the API answers
404 for is absent; any other refusal stops the run.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import re
from typing import Any

from bootstrap.forgejo import Forgejo, ForgejoError

PLATFORM_ORG = "unicon"
PLATFORM_ORG_DESCRIPTION = "Unicon's own workflows and primitives"
DEFAULT_BRANCH = "main"

PAGE_SIZE = 50
TREE_PAGE_SIZE = 1000

_VERSION = re.compile(r"^v([1-9][0-9]*)$")


class Outcome(enum.Enum):
    """What `publish` did about one repository's version."""

    PRESENT = "present"
    CREATED = "created"
    REWRITTEN = "rewritten"


class Standing(enum.Enum):
    """How a version at the forge stands against the files for it here."""

    ABSENT = "absent"
    SAME = "same"
    DIFFERENT = "different"


class VersionDiffers(ValueError):
    """A version at the forge holds other files than the ones for it here,
    and a version is never edited without being told to.
    """


def version_number(version: str) -> int | None:
    """The number of a version tag `vN`, or None for any other tag."""
    match = _VERSION.match(version)
    return int(match.group(1)) if match else None


def blob_id(content: bytes, length: int = 40) -> str:
    """The git object id of a file with these bytes: SHA-1 for a 40
    character id, SHA-256 for a repository in the 64 character format.
    """
    header = f"blob {len(content)}\0".encode()
    digest = hashlib.sha256 if length == 64 else hashlib.sha1
    return digest(header + content).hexdigest()


class PlatformRepos:
    def __init__(self, forgejo: Forgejo, token: str) -> None:
        self._forgejo = forgejo
        self._token = token

    def ensure_platform_org(self) -> bool:
        """Returns True if this run created the platform org.

        Made with the admin token, so its Owners team holds the
        platform account and nobody else. Limited, not private: Forgejo hides
        every repository of a private organisation from non-members, public
        ones included, and the built-in workflows and primitives are there to
        be read from every org.
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

    def ensure_mark(self, org: str, repo: str, topic: str) -> bool:
        """Returns True if this run put the topic on the repository."""
        listed = _ok(self._call("GET", f"/repos/{org}/{repo}/topics"))
        if topic in (listed.get("topics") or []):
            return False
        _ok(self._call("PUT", f"/repos/{org}/{repo}/topics/{topic}"))
        return True

    def versions(self, org: str, repo: str) -> dict[str, str]:
        """Every version tag of the repository and the commit it points at,
        oldest version first. Tags that are not `vN` are left out, and a
        repository that is not there has none.
        """
        found: dict[str, str] = {}
        page = 1
        while True:
            listed = self._found(
                "GET", f"/repos/{org}/{repo}/tags?page={page}&limit={PAGE_SIZE}"
            )
            for tag in listed or []:
                name = str(tag["name"])
                if version_number(name) is not None:
                    found[name] = str(tag["commit"]["sha"])
            if not listed or len(listed) < PAGE_SIZE:
                break
            page += 1
        return dict(sorted(found.items(), key=lambda item: _number(item[0])))

    def head(self, org: str, repo: str) -> str | None:
        """The commit at the tip of the default branch, or None for a
        repository with no commit yet.
        """
        branch = self._found("GET", f"/repos/{org}/{repo}/branches/{DEFAULT_BRANCH}")
        return None if branch is None else str(branch["commit"]["id"])

    def blobs(self, org: str, repo: str, commit: str) -> dict[str, str]:
        """Every file at the commit, as path to git blob id."""
        found: dict[str, str] = {}
        page = 1
        while True:
            listed = _ok(
                self._call(
                    "GET",
                    f"/repos/{org}/{repo}/git/trees/{commit}"
                    f"?recursive=true&page={page}&per_page={TREE_PAGE_SIZE}",
                )
            )
            entries = listed.get("tree") or []
            for entry in entries:
                if entry["type"] == "blob":
                    found[str(entry["path"])] = str(entry["sha"])
            if not entries or page * TREE_PAGE_SIZE >= int(listed["total_count"]):
                break
            page += 1
        return found

    def read_file(self, org: str, repo: str, path: str, ref: str) -> bytes | None:
        """A file's bytes at a tag or commit, or None when it is not there."""
        entry = self._found("GET", f"/repos/{org}/{repo}/contents/{path}?ref={ref}")
        if entry is None or entry.get("type") != "file":
            return None
        return base64.b64decode(entry["content"])

    def write_tree(
        self, org: str, repo: str, files: dict[str, bytes], message: str
    ) -> tuple[str, bool]:
        """Bring the default branch to exactly these files, in one commit.
        Returns the head commit and whether this run made a commit: a branch
        that already holds exactly these files is left as it is.
        """
        head = self.head(org, repo)
        if head is None:
            operations = [
                _operation("create", path, content)
                for path, content in sorted(files.items())
            ]
            return self._commit(org, repo, operations, message, new_branch=True), True
        present = self.blobs(org, repo, head)
        operations = []
        for path, content in sorted(files.items()):
            existing = present.get(path)
            if existing is None:
                operations.append(_operation("create", path, content))
            elif existing != blob_id(content, len(existing)):
                operations.append(_operation("update", path, content, existing))
        for path in sorted(set(present) - set(files)):
            operations.append(_operation("delete", path, sha=present[path]))
        if not operations:
            return head, False
        return self._commit(org, repo, operations, message, new_branch=False), True

    def holds(
        self,
        org: str,
        repo: str,
        commit: str,
        files: dict[str, bytes],
        only: tuple[str, ...] | None = None,
    ) -> bool:
        """Whether the commit holds exactly these files, no more and no fewer,
        or, given `only`, holds those of the files with the same content.
        """
        present = self.blobs(org, repo, commit)
        if only is not None:
            return all(
                path in present
                and path in files
                and present[path] == blob_id(files[path], len(present[path]))
                for path in only
            )
        if set(present) != set(files):
            return False
        return all(
            present[path] == blob_id(content, len(present[path]))
            for path, content in files.items()
        )

    def tag(self, org: str, repo: str, version: str, target: str) -> None:
        _ok(
            self._call(
                "POST",
                f"/repos/{org}/{repo}/tags",
                body={"tag_name": version, "target": target},
            )
        )

    def move_tag(self, org: str, repo: str, version: str, target: str) -> None:
        """Point an existing tag at another commit. Forgejo has no call that
        moves a tag, so it is deleted and made again.
        """
        _ok(self._call("DELETE", f"/repos/{org}/{repo}/tags/{version}"))
        self.tag(org, repo, version, target)

    def _commit(
        self,
        org: str,
        repo: str,
        operations: list[dict[str, str]],
        message: str,
        *,
        new_branch: bool,
    ) -> str:
        body: dict[str, Any] = {
            "branch": DEFAULT_BRANCH,
            "message": message,
            "files": operations,
        }
        if new_branch:
            body["new_branch"] = DEFAULT_BRANCH
        written = _ok(self._call("POST", f"/repos/{org}/{repo}/contents", body=body))
        return str(written["commit"]["sha"])

    def _found(self, method: str, path: str) -> Any | None:
        """The body of an answer, or None when Forgejo has no such thing."""
        status, body = self._call(method, path)
        if status == 404:
            return None
        return _ok((status, body))

    def _call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        return self._forgejo.api(method, path, token=self._token, body=body)


def standing(
    repos: PlatformRepos,
    org: str,
    repo: str,
    version: str,
    files: dict[str, bytes],
    identity: tuple[str, ...] | None = None,
) -> Standing:
    """Whether the tag `version` is absent, holds these files, or differs,
    read without writing anything. A tag holds the files when its `identity`
    files match, every file when `identity` is not given, whatever the
    others say.
    """
    commit = repos.versions(org, repo).get(version)
    if commit is None:
        return Standing.ABSENT
    if repos.holds(org, repo, commit, files, identity):
        return Standing.SAME
    return Standing.DIFFERENT


def publish(
    repos: PlatformRepos,
    org: str,
    repo: str,
    version: str,
    files: dict[str, bytes],
    *,
    message: str,
    rewrite: bool = False,
    identity: tuple[str, ...] | None = None,
) -> Outcome:
    """Make sure the tag `version` holds these files, and return what was
    done.

    With no such tag, the files are committed and tagged. A tag that holds
    them, by `standing`, is left as it is. One that differs is refused with
    `VersionDiffers`, unless `rewrite`, the development-only exception, which
    moves it to a commit of the files. Whichever commit is made holds every
    file.
    """
    found = standing(repos, org, repo, version, files, identity)
    if found is Standing.SAME:
        return Outcome.PRESENT
    if found is Standing.DIFFERENT:
        if not rewrite:
            raise VersionDiffers(
                f"{org}/{repo}@{version} at the forge differs from the files "
                "here, and a version is never edited"
            )
        head, _ = repos.write_tree(org, repo, files, message)
        repos.move_tag(org, repo, version, head)
        return Outcome.REWRITTEN
    head, _ = repos.write_tree(org, repo, files, message)
    repos.tag(org, repo, version, head)
    return Outcome.CREATED


def _number(version: str) -> int:
    """The number of a tag `versions` returned, all of which are `vN`."""
    return version_number(version) or 0


def _operation(
    kind: str, path: str, content: bytes | None = None, sha: str | None = None
) -> dict[str, str]:
    operation = {"operation": kind, "path": path}
    if content is not None:
        operation["content"] = base64.b64encode(content).decode()
    if sha is not None:
        operation["sha"] = sha
    return operation


def _ok(answer: tuple[int, Any]) -> Any:
    status, body = answer
    if status >= 400:
        raise ForgejoError(f"Forgejo answered {status}: {str(body)[:200]}")
    return body
