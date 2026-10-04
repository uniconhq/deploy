"""The image manifest: every image the stack grades with, by digest.

`images.json` in this repo is the release manifest. It pins the runner's
images, which the stack runs, and each primitive: the image its steps run
from, and where the primitive's own repository is at that release, since
bootstrap mirrors that repository into the forge (primitives.py).

    {
      "images": {
        "harness": "ghcr.io/uniconhq/harness@sha256:<64 hex>",
        "clone": "...", "socket-filter": "...", "worker": "..."
      },
      "primitives": {
        "compile": {
          "image": "ghcr.io/uniconhq/primitive-compile@sha256:<64 hex>",
          "source": {"github": "uniconhq/primitive-compile", "tag": "v1.0.0"}
        }
      }
    }

A development machine builds every image from the sibling checkouts instead,
pushes them to a registry on localhost to learn their digests, and writes
`images.local.json` in the same shape (scripts/build-images.py). There a
primitive's source is `{"path": "../primitive-compile"}`, a checkout relative
to the deploy directory. Bootstrap reads the local manifest when it is there
and the release manifest otherwise, unless told which with --images.

Every image is a reference by digest and never by tag, because what a stack
grades with must not change under it: a tag can be moved, a digest cannot.
A manifest that names a tag, or lacks one of the three images bootstrap hands
the services, is refused before anything starts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RELEASE_MANIFEST = "images.json"
LOCAL_MANIFEST = "images.local.json"

HARNESS = "harness"
CLONE = "clone"
SOCKET_FILTER = "socket-filter"
REQUIRED_IMAGES = (HARNESS, CLONE, SOCKET_FILTER)

_BY_DIGEST = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+"
    r"@sha256:[0-9a-f]{64}$"
)
_PRIMITIVE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ManifestError(ValueError):
    """The image manifest is missing or does not have the shape above."""


@dataclass(frozen=True)
class GitHubSource:
    """A primitive repository on GitHub at a tag."""

    repo: str
    tag: str


@dataclass(frozen=True)
class LocalSource:
    """A primitive repository checked out on this machine."""

    path: Path


@dataclass(frozen=True)
class PrimitiveRelease:
    name: str
    image: str
    source: GitHubSource | LocalSource


@dataclass(frozen=True)
class Manifest:
    path: Path
    images: dict[str, str]
    primitives: tuple[PrimitiveRelease, ...]

    @property
    def harness(self) -> str:
        return self.images[HARNESS]

    @property
    def clone(self) -> str:
        return self.images[CLONE]

    @property
    def socket_filter(self) -> str:
        return self.images[SOCKET_FILTER]


def is_by_digest(reference: str) -> bool:
    return bool(_BY_DIGEST.match(reference))


def choose(directory: Path, given: Path | None) -> Path:
    """The manifest this run reads: the one asked for, else the local one
    when a build has written it, else the release manifest.
    """
    if given is not None:
        return given if given.is_absolute() else directory / given
    local = directory / LOCAL_MANIFEST
    return local if local.exists() else directory / RELEASE_MANIFEST


def load(path: Path) -> Manifest:
    """Read and check a manifest. A local source's path is resolved against
    the directory the manifest is in, which is the deploy directory.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"{path} is missing") from None
    except json.JSONDecodeError as failure:
        raise ManifestError(f"{path} is not JSON: {failure}") from None
    if not isinstance(document, dict):
        raise ManifestError(f"{path}: the top level is not an object")
    images = _images(path, document.get("images"))
    primitives = tuple(
        _primitive(path, name, entry)
        for name, entry in sorted(_object(path, "primitives", document).items())
    )
    return Manifest(path=path, images=images, primitives=primitives)


def _images(path: Path, value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ManifestError(f"{path}: `images` is not an object")
    images: dict[str, str] = {}
    for name, reference in value.items():
        images[str(name)] = _digest(path, f"images.{name}", reference)
    missing = [name for name in REQUIRED_IMAGES if name not in images]
    if missing:
        raise ManifestError(f"{path}: `images` has no {', '.join(missing)}")
    return images


def _primitive(path: Path, name: str, entry: Any) -> PrimitiveRelease:
    where = f"primitives.{name}"
    if not _PRIMITIVE_NAME.match(name):
        raise ManifestError(f"{path}: {where} is not a primitive name")
    if not isinstance(entry, dict):
        raise ManifestError(f"{path}: {where} is not an object")
    image = _digest(path, f"{where}.image", entry.get("image"))
    source = entry.get("source")
    if not isinstance(source, dict):
        raise ManifestError(f"{path}: {where}.source is not an object")
    if set(source) == {"github", "tag"}:
        repo, tag = source["github"], source["tag"]
        if not isinstance(repo, str) or not _GITHUB_REPO.match(repo):
            raise ManifestError(f"{path}: {where}.source.github is not owner/name")
        if not isinstance(tag, str) or not tag:
            raise ManifestError(f"{path}: {where}.source.tag is empty")
        return PrimitiveRelease(name, image, GitHubSource(repo, tag))
    if set(source) == {"path"} and isinstance(source["path"], str):
        return PrimitiveRelease(
            name, image, LocalSource((path.parent / source["path"]).resolve())
        )
    raise ManifestError(
        f"{path}: {where}.source is neither {{github, tag}} nor {{path}}"
    )


def _digest(path: Path, where: str, reference: Any) -> str:
    if not isinstance(reference, str) or not is_by_digest(reference):
        raise ManifestError(
            f"{path}: {where} is not an image reference by digest "
            "(name@sha256:<64 hex>)"
        )
    return reference


def _object(path: Path, key: str, document: dict[str, Any]) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"{path}: `{key}` is not an object")
    return value
