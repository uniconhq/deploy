"""The three primitives the forge is seeded with, mirrored from their repos.

A primitive is one grading step: a program in an image, and a declaration,
`primitive.yaml`, of its inputs, outputs and limits. Each is developed in its
own repository, `primitive-<name>`, and the forge holds a copy in the platform
org as the public repository `unicon/<name>.primitive`, carrying the topic
`unicon-primitive`, with a tag per version. The forge package's compiler reads
`primitive.yaml` at the root of that repository at the version a workflow
names, and nothing else of it; the rest is there so the program behind a step
can be read where the step is used.

What is mirrored is the primitive's repository as the image manifest names it
(images.py): on GitHub at the release tag, or, on a development machine, the
sibling checkout as it is on disk, every file git does not ignore. The
repository's own CI configuration under .github/ is left out, since it means
nothing at the forge.

The declaration in the primitive's repository has no `image` line: which image
a version runs is the deployment's decision, from the manifest. Bootstrap
writes `version` and `image` into the copy it commits, right under `name`, so
the file names the tag it is under and the image by its digest. Nothing at the
forge ever names an image by tag.

A version is what its declaration says, the image included, since that is
what the forge compiles against and what a step runs. A version is frozen, so
a changed digest in the manifest, or any other change to the declaration,
makes the next version, v2 and on, holding the repository as it is then, and
never edits the old one: a published task keeps grading against what it was
published with. A change to the other files alone makes no version, because
it changes nothing that grades: the program that runs is the one in the
image, and a rebuilt image is a new digest. A rerun with nothing changed makes
nothing. The one exception is a development stack, where --rewrite-v1
rewrites v1 in place instead.
"""

from __future__ import annotations

import io
import re
import subprocess
import tarfile
from pathlib import PurePosixPath

import httpx

from bootstrap.images import GitHubSource, LocalSource, PrimitiveRelease
from bootstrap.platform_repos import PLATFORM_ORG, Outcome, PlatformRepos, publish

PRIMITIVE_TOPIC = "unicon-primitive"
DECLARATION = "primitive.yaml"
REQUIRED_FILES = (DECLARATION, "Dockerfile")
LEFT_OUT = (".github/",)
BUILD_LEFTOVERS = (
    "__pycache__/",
    "*.pyc",
    ".venv/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
)
MAX_MIRROR_BYTES = 8 * 1024 * 1024

_TOP_LEVEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$")
_WRITTEN_KEYS = ("version", "image")


class PrimitiveError(ValueError):
    """A primitive's repository cannot be mirrored as it is."""


def repository(name: str) -> str:
    return f"{name}.primitive"


def source_files(release: PrimitiveRelease) -> dict[str, bytes]:
    """The primitive repository's files, by path, as the manifest names it."""
    if isinstance(release.source, LocalSource):
        files = _checkout_files(release.source)
        where = str(release.source.path)
    else:
        files = _github_files(release.source)
        where = f"{release.source.repo}@{release.source.tag}"
    kept = {
        path: content
        for path, content in files.items()
        if not path.startswith(LEFT_OUT)
    }
    missing = [path for path in REQUIRED_FILES if path not in kept]
    if missing:
        raise PrimitiveError(f"{where} has no {', '.join(missing)}")
    size = sum(len(content) for content in kept.values())
    if size > MAX_MIRROR_BYTES:
        raise PrimitiveError(
            f"{where} holds {size} bytes; a primitive repository is its "
            f"declaration, its Dockerfile and its program, and more than "
            f"{MAX_MIRROR_BYTES} bytes is not mirrored"
        )
    return kept


def with_declaration(
    files: dict[str, bytes], name: str, version: str, image: str
) -> dict[str, bytes]:
    """The files to commit for a version: the repository's files with the
    declaration naming that version and image.
    """
    return {
        **files,
        DECLARATION: declaration(files[DECLARATION], name, version, image),
    }


def declaration(source: bytes, name: str, version: str, image: str) -> bytes:
    """The declaration with `version` and `image` written in under `name`.

    Any `version` or `image` line already there is replaced, so a copy is
    the same whatever the repository's file said. The file is edited as text
    rather than parsed and written out again, so its comments and layout are
    the author's. It has to name the primitive the manifest has it under.
    """
    text = source.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    kept: list[str] = []
    declared: str | None = None
    for index, line in enumerate(lines):
        match = _TOP_LEVEL.match(line)
        key = match.group(1) if match else None
        if key in _WRITTEN_KEYS:
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if following[:1] in (" ", "\t"):
                raise PrimitiveError(
                    f"{DECLARATION} of {name}: `{key}` is not a single line"
                )
            continue
        if key == "name" and match is not None:
            declared = _scalar(match.group(2))
            kept += [line, f"version: {version}", f"image: {image}"]
            continue
        kept.append(line)
    expected = f"{PLATFORM_ORG}/{name}"
    if declared != expected:
        raise PrimitiveError(
            f"{DECLARATION} of {name} names {declared!r}, not {expected!r}"
        )
    return (newline.join(kept) + newline).encode("utf-8")


def image_of(declaration_bytes: bytes) -> str | None:
    """The image a declaration names, from its top-level `image` line."""
    for line in declaration_bytes.decode("utf-8", "replace").splitlines():
        match = _TOP_LEVEL.match(line)
        if match and match.group(1) == "image":
            return _scalar(match.group(2)) or None
    return None


def mirror(
    repos: PlatformRepos, release: PrimitiveRelease, *, rewrite_first: bool
) -> tuple[str, Outcome]:
    """Make the primitive's latest version hold its repository with the
    manifest's image, and return that version and what was done.
    """
    files = source_files(release)
    return publish(
        repos,
        PLATFORM_ORG,
        repository(release.name),
        lambda version: with_declaration(files, release.name, version, release.image),
        message=lambda version: (
            f"Mirror {PLATFORM_ORG}/{release.name}@{version} with {release.image}"
        ),
        next_version_on_change=True,
        rewrite_first=rewrite_first,
        identity=(DECLARATION,),
    )


def images_at_forge(repos: PlatformRepos, names: list[str]) -> list[str]:
    """Every image any version of these primitives names at the forge,
    sorted and without repeats: the list a socket filter lets containers be
    made from, since a task published against an older version still runs
    that version's image.
    """
    found: set[str] = set()
    for name in names:
        repo = repository(name)
        for version in repos.versions(PLATFORM_ORG, repo):
            content = repos.read_file(PLATFORM_ORG, repo, DECLARATION, version)
            image = image_of(content) if content is not None else None
            if image:
                found.add(image)
    return sorted(found)


def _scalar(value: str) -> str:
    """A plain or quoted one-line YAML scalar, without a trailing comment."""
    text = value.strip()
    if text[:1] in ("'", '"'):
        closing = text.find(text[0], 1)
        return text[1:closing] if closing > 0 else text[1:]
    return text.split(" #", 1)[0].strip()


def _checkout_files(source: LocalSource) -> dict[str, bytes]:
    """Every file of the checkout that git does not ignore, committed or not,
    because a development checkout is built as it is on disk. What a test
    run or a virtual environment leaves behind is left out even where the
    checkout does not ignore it yet, or every test run would be a new
    version.
    """
    if not source.path.is_dir():
        raise PrimitiveError(f"{source.path} is not a directory")
    listed = subprocess.run(
        [
            "git",
            "-C",
            str(source.path),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            *(f"--exclude={pattern}" for pattern in BUILD_LEFTOVERS),
        ],
        capture_output=True,
    )
    if listed.returncode != 0:
        raise PrimitiveError(f"{source.path} is not a git checkout")
    files: dict[str, bytes] = {}
    for raw in listed.stdout.decode("utf-8").split("\0"):
        if not raw:
            continue
        on_disk = source.path / raw
        if on_disk.is_file():
            files[_safe_path(raw)] = on_disk.read_bytes()
    return files


def _github_files(source: GitHubSource) -> dict[str, bytes]:
    """The repository's files at the tag, from the archive GitHub serves for
    it. Read in memory and never unpacked to disk.
    """
    url = f"https://codeload.github.com/{source.repo}/tar.gz/refs/tags/{source.tag}"
    response = httpx.get(url, follow_redirects=True, timeout=60.0)
    if response.status_code != 200:
        raise PrimitiveError(
            f"{source.repo}@{source.tag}: GitHub answered {response.status_code}"
        )
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            _, _, inner = member.name.partition("/")
            extracted = archive.extractfile(member)
            if inner and extracted is not None:
                files[_safe_path(inner)] = extracted.read()
    return files


def _safe_path(path: str) -> str:
    """A path inside the repository, refused if it could name anything
    outside it.
    """
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise PrimitiveError(f"{path} is not a path inside a repository")
    return pure.as_posix()
