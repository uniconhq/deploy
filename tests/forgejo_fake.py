"""A Forgejo that answers the API calls bootstrap makes for the platform org,
holding its organisations and repositories in memory: each repository's
commits as whole file trees, its default branch, its tags and its topics.

It stands in for Compose, the way bootstrap reaches Forgejo: its exec reads
the curl configuration bootstrap wrote to standard input and answers with a
body and a status the way curl's write-out prints them. Every request is kept,
so a test can say which calls a run made and with what. A write the real
Forgejo would refuse, such as an update naming the wrong blob, fails the test.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bootstrap.compose import Compose
from bootstrap.forgejo import API_INSIDE_THE_CONTAINER
from bootstrap.platform_repos import blob_id

ABSENT = (404, {"message": "not found"})


def read_config(stdin: str) -> dict[str, list[str]]:
    """The curl configuration bootstrap wrote, as name to values."""
    fields: dict[str, list[str]] = {}
    for line in stdin.splitlines():
        name, _, value = line.partition(" = ")
        fields.setdefault(name, []).append(json.loads(value) if value else "")
    return fields


@dataclass
class FakeRepo:
    private: bool = False
    default_branch: str = "main"
    commits: dict[str, dict[str, bytes]] = field(default_factory=dict)
    head: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    topics: list[str] = field(default_factory=list)

    def commit(self, files: dict[str, bytes]) -> str:
        """Record a commit holding exactly these files on the branch."""
        sha = hashlib.sha1(
            f"{len(self.commits)}:{sorted(files.items())}".encode()
        ).hexdigest()
        self.commits[sha] = dict(files)
        self.head = sha
        return sha

    def files_at(self, ref: str) -> dict[str, bytes]:
        return self.commits[self.tags.get(ref, ref)]


class FakeForgejo(Compose):
    def __init__(self) -> None:
        super().__init__(Path("."), [])
        self.orgs: set[str] = set()
        self.repos: dict[str, FakeRepo] = {}
        self.requests: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, str, Any]] = []
        self.configs: list[dict[str, list[str]]] = []
        self.refusal: int | None = None

    def seed(
        self,
        full_name: str,
        files: dict[str, bytes] | None = None,
        *,
        tags: tuple[str, ...] = (),
        topics: tuple[str, ...] = (),
    ) -> FakeRepo:
        """A repository already there, with one commit of `files` if given
        and the tags pointing at it.
        """
        self.orgs.add(full_name.split("/")[0])
        repo = FakeRepo(topics=list(topics))
        if files is not None:
            sha = repo.commit(files)
            repo.tags = dict.fromkeys(tags, sha)
        self.repos[full_name] = repo
        return repo

    def writes(self) -> list[tuple[str, str]]:
        return [request for request in self.requests if request[0] != "GET"]

    def body_of(self, method: str, path: str) -> Any:
        """The body of the last request with this method and path."""
        for seen_method, seen_path, body in reversed(self.bodies):
            if (seen_method, seen_path) == (method, path):
                return body
        raise AssertionError(f"no {method} {path} was made")

    def execute(
        self,
        service: str,
        *arguments: str,
        user: str | None = None,
        stdin: str | None = None,
    ) -> str:
        assert arguments == ("curl", "--config", "-")
        assert stdin is not None
        config = read_config(stdin)
        self.configs.append(config)
        method = config["request"][0]
        url = urlsplit(config["url"][0].removeprefix(API_INSIDE_THE_CONTAINER))
        body = json.loads(config["data"][0]) if "data" in config else None
        self.requests.append((method, url.path))
        self.bodies.append((method, url.path, body))
        query = {name: values[0] for name, values in parse_qs(url.query).items()}
        if self.refusal is not None:
            status, answer = self.refusal, {"message": "refused"}
        else:
            status, answer = self._answer(method, url.path.split("/")[1:], query, body)
        return f"{json.dumps(answer) if answer is not None else ''}\n{status}"

    def _answer(
        self, method: str, parts: list[str], query: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        if parts[0] == "orgs":
            return self._orgs(method, parts[1:], body)
        if parts[0] == "repos" and len(parts) >= 3:
            full_name = f"{parts[1]}/{parts[2]}"
            if full_name not in self.repos:
                return ABSENT
            return self._repo(method, self.repos[full_name], parts[3:], query, body)
        raise AssertionError(f"unexpected {method} /{'/'.join(parts)}")

    def _orgs(self, method: str, rest: list[str], body: Any) -> tuple[int, Any]:
        if method == "POST" and not rest:
            self.orgs.add(body["username"])
            return 201, {"username": body["username"]}
        if method == "GET" and len(rest) == 1:
            return (200, {"username": rest[0]}) if rest[0] in self.orgs else ABSENT
        if method == "POST" and len(rest) == 2 and rest[1] == "repos":
            full_name = f"{rest[0]}/{body['name']}"
            assert full_name not in self.repos, f"{full_name} made twice"
            self.repos[full_name] = FakeRepo(
                private=body["private"], default_branch=body["default_branch"]
            )
            return 201, {"name": body["name"]}
        raise AssertionError(f"unexpected {method} /orgs/{'/'.join(rest)}")

    def _repo(
        self,
        method: str,
        repo: FakeRepo,
        rest: list[str],
        query: dict[str, str],
        body: Any,
    ) -> tuple[int, Any]:
        route = (method, rest[0] if rest else "")
        if route == ("GET", ""):
            return 200, {"private": repo.private}
        if route == ("GET", "branches"):
            if repo.head is None or rest[1] != repo.default_branch:
                return ABSENT
            return 200, {"name": rest[1], "commit": {"id": repo.head}}
        if route == ("POST", "contents"):
            return self._write(repo, body)
        if route == ("GET", "contents"):
            ref = query["ref"]
            if ref not in repo.tags and ref not in repo.commits:
                return ABSENT
            path = "/".join(rest[1:])
            content = repo.files_at(ref).get(path)
            if content is None:
                return ABSENT
            return 200, {"type": "file", "content": base64.b64encode(content).decode()}
        if route == ("GET", "topics"):
            return 200, {"topics": list(repo.topics)}
        if route == ("PUT", "topics"):
            repo.topics.append(rest[1])
            return 204, None
        if route == ("GET", "tags"):
            limit, page = int(query["limit"]), int(query["page"])
            listed = [
                {"name": name, "commit": {"sha": sha}}
                for name, sha in sorted(repo.tags.items())
            ]
            return 200, listed[(page - 1) * limit : page * limit]
        if route == ("POST", "tags"):
            if body["tag_name"] in repo.tags:
                return 409, {"message": "tag exists"}
            assert body["target"] in repo.commits, "a tag must point at a commit"
            repo.tags[body["tag_name"]] = body["target"]
            return 201, {"name": body["tag_name"]}
        if route == ("DELETE", "tags"):
            if rest[1] not in repo.tags:
                return ABSENT
            del repo.tags[rest[1]]
            return 204, None
        if route == ("GET", "git") and rest[1] == "trees":
            files = repo.commits.get(rest[2])
            if files is None:
                return ABSENT
            tree = [
                {"path": path, "type": "blob", "sha": blob_id(content)}
                for path, content in sorted(files.items())
            ]
            return 200, {
                "tree": tree,
                "page": 1,
                "truncated": False,
                "total_count": len(tree),
            }
        raise AssertionError(f"unexpected {method} {'/'.join(rest)}")

    def _write(self, repo: FakeRepo, body: Any) -> tuple[int, Any]:
        """One commit of create, update and delete operations, refused the
        way Forgejo refuses them.
        """
        if repo.head is None:
            assert body.get("new_branch") == repo.default_branch
            files: dict[str, bytes] = {}
        else:
            assert "new_branch" not in body, "the branch already exists"
            files = dict(repo.commits[repo.head])
        assert body["branch"] == repo.default_branch
        for operation in body["files"]:
            path = operation["path"]
            kind = operation["operation"]
            if kind == "create":
                assert path not in files, f"{path} exists"
            else:
                assert path in files, f"{path} is not there to {kind}"
                assert operation["sha"] == blob_id(files[path]), "stale sha"
            if kind == "delete":
                del files[path]
            else:
                files[path] = base64.b64decode(operation["content"])
        return 201, {"commit": {"sha": repo.commit(files)}}
