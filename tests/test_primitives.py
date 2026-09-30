"""Mirroring the primitives into the platform org: the declaration names the
manifest's image and the version it is under, a rerun with nothing changed
makes nothing, and a changed digest makes the next version and leaves the
previous one as it was.

The forge package reads a primitive as `unicon/<name>.primitive`, public,
carrying the topic `unicon-primitive`, with `primitive.yaml` at the root of
each version tag. These tests run bootstrap's seeding step against the
in-memory Forgejo of forgejo_fake.py, with primitive checkouts made in a
temporary directory.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from bootstrap import main, primitives
from bootstrap.images import GitHubSource, LocalSource, Manifest, PrimitiveRelease
from bootstrap.primitives import PrimitiveError
from bootstrap.summary import Summary
from tests.forgejo_fake import FakeForgejo

DEPLOY = Path(__file__).resolve().parent.parent
TOKEN = "provisioning-token"
FIRST = "localhost:5000/uniconhq/primitive-compile@sha256:" + "1" * 64
SECOND = "localhost:5000/uniconhq/primitive-compile@sha256:" + "2" * 64
RUNNER = "@sha256:" + "0" * 64
DECLARATION = b"""# The compile primitive.
name: unicon/compile
entrypoint: [/usr/local/bin/compile]
batch: false
inputs:
  source: {type: file}
"""
FULL = "unicon/compile.primitive"


def _checkout(root: Path, files: dict[str, bytes]) -> Path:
    """A git checkout holding these files, none of them committed."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for path, content in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(content)
    return root


def _compile(tmp_path: Path, **extra: bytes) -> Path:
    return _checkout(
        tmp_path / "primitive-compile",
        {
            "primitive.yaml": DECLARATION,
            "Dockerfile": b"FROM scratch\n",
            "compile.py": b"print('compile')\n",
            **extra,
        },
    )


def _manifest(checkout: Path, image: str) -> Manifest:
    return Manifest(
        path=DEPLOY / "images.local.json",
        images={
            name: f"localhost:5000/uniconhq/{name}{RUNNER}"
            for name in ("harness", "clone", "socket-filter")
        },
        primitives=(PrimitiveRelease("compile", image, LocalSource(checkout)),),
    )


def _seed(
    forge: FakeForgejo, manifest: Manifest, rewrite: bool = False
) -> tuple[Summary, dict[str, str]]:
    summary = Summary()
    values = {"UNICON_FORGE_ADMIN_TOKEN": TOKEN}
    main._seed_platform(forge, DEPLOY, manifest, values, summary, rewrite)
    return summary, values


def test_a_fresh_forge_gets_the_primitive_at_v1(tmp_path: Path) -> None:
    forge = FakeForgejo()

    summary, values = _seed(forge, _manifest(_compile(tmp_path), FIRST))

    repo = forge.repos[FULL]
    assert not repo.private
    assert repo.topics == ["unicon-primitive"]
    assert list(repo.tags) == ["v1"]
    files = repo.files_at("v1")
    assert set(files) == {"primitive.yaml", "Dockerfile", "compile.py"}
    assert files["primitive.yaml"].decode().splitlines()[:4] == [
        "# The compile primitive.",
        "name: unicon/compile",
        "version: v1",
        f"image: {FIRST}",
    ]
    assert "  created  primitive unicon/compile version v1" in summary.lines()
    assert values["UNICON_FILTER_IMAGES"] == FIRST


def test_a_rerun_with_nothing_changed_makes_nothing(tmp_path: Path) -> None:
    forge = FakeForgejo()
    manifest = _manifest(_compile(tmp_path), FIRST)
    _seed(forge, manifest)
    forge.requests.clear()

    summary, _ = _seed(forge, manifest)

    assert forge.writes() == []
    assert summary.lines()[0].startswith("created: 0,")


def test_a_changed_digest_makes_v2_and_leaves_v1(tmp_path: Path) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(checkout, FIRST))
    repo = forge.repos[FULL]
    first = repo.tags["v1"]
    v1_files = repo.files_at("v1")

    summary, values = _seed(forge, _manifest(checkout, SECOND))

    assert repo.tags["v1"] == first
    assert repo.files_at("v1") == v1_files
    declaration = repo.files_at("v2")["primitive.yaml"].decode()
    assert "version: v2" in declaration.splitlines()
    assert f"image: {SECOND}" in declaration.splitlines()
    assert "  created  primitive unicon/compile version v2" in summary.lines()
    assert values["UNICON_FILTER_IMAGES"] == f"{FIRST},{SECOND}"


def test_a_changed_program_alone_makes_no_version(tmp_path: Path) -> None:
    """The program that runs is the one in the image, so without a new
    digest nothing that grades has changed.
    """
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(checkout, FIRST))
    (checkout / "compile.py").write_bytes(b"print('changed')\n")
    forge.requests.clear()

    _seed(forge, _manifest(checkout, FIRST))

    assert forge.writes() == []
    assert list(forge.repos[FULL].tags) == ["v1"]


def test_the_next_version_holds_the_repository_as_it_is_then(
    tmp_path: Path,
) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(checkout, FIRST))
    (checkout / "compile.py").unlink()
    (checkout / "main.py").write_bytes(b"print('main')\n")

    _seed(forge, _manifest(checkout, SECOND))

    repo = forge.repos[FULL]
    assert set(repo.files_at("v2")) == {"primitive.yaml", "Dockerfile", "main.py"}
    assert "compile.py" in repo.files_at("v1")


def test_a_changed_declaration_makes_the_next_version(tmp_path: Path) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(checkout, FIRST))
    (checkout / "primitive.yaml").write_bytes(DECLARATION + b"batch_note: x\n")

    _seed(forge, _manifest(checkout, FIRST))

    assert list(forge.repos[FULL].tags) == ["v1", "v2"]


def test_rewrite_on_a_development_stack_moves_v1(tmp_path: Path) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(checkout, FIRST))

    _seed(forge, _manifest(checkout, SECOND), rewrite=True)

    repo = forge.repos[FULL]
    assert list(repo.tags) == ["v1"]
    assert f"image: {SECOND}" in repo.files_at("v1")["primitive.yaml"].decode()


def test_the_declaration_names_the_version_and_the_image_under_its_name() -> None:
    source = (
        b"name: 'unicon/compile'  # the name\r\n"
        b"version: v9\r\nimage: ghcr.io/x/y:latest\r\nbatch: false\r\n"
    )

    written = primitives.declaration(source, "compile", "v3", FIRST).decode()

    assert written.split("\r\n") == [
        "name: 'unicon/compile'  # the name",
        "version: v3",
        f"image: {FIRST}",
        "batch: false",
        "",
    ]
    assert primitives.image_of(written.encode()) == FIRST


def test_a_declaration_for_another_primitive_is_refused() -> None:
    with pytest.raises(PrimitiveError, match="unicon/run"):
        primitives.declaration(b"name: unicon/run\n", "compile", "v1", FIRST)


def test_an_image_over_several_lines_is_refused() -> None:
    source = b"name: unicon/compile\nimage:\n  ghcr.io/x\n"

    with pytest.raises(PrimitiveError, match="single line"):
        primitives.declaration(source, "compile", "v1", FIRST)


def test_ignored_files_leftovers_and_github_ci_are_not_mirrored(tmp_path: Path) -> None:
    checkout = _compile(
        tmp_path,
        **{
            ".gitignore": b"build/\n",
            "build/out.bin": b"\0",
            ".github/workflows/ci.yaml": b"on: push\n",
            "tests/test_compile.py": b"",
            "tests/__pycache__/test_compile.cpython-314.pyc": b"pyc",
            "src/stale.pyc": b"pyc",
        },
    )
    release = PrimitiveRelease("compile", FIRST, LocalSource(checkout))

    assert set(primitives.source_files(release)) == {
        ".gitignore",
        "primitive.yaml",
        "Dockerfile",
        "compile.py",
        "tests/test_compile.py",
    }


def test_a_repository_without_a_dockerfile_is_refused(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path / "p", {"primitive.yaml": DECLARATION})

    with pytest.raises(PrimitiveError, match="Dockerfile"):
        primitives.source_files(
            PrimitiveRelease("compile", FIRST, LocalSource(checkout))
        )


def _archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_a_release_is_read_from_the_github_archive_of_its_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    def get(url: str, **_: Any) -> httpx.Response:
        asked.append(url)
        return httpx.Response(
            200,
            content=_archive(
                {
                    "primitive-compile-0.1.0/primitive.yaml": DECLARATION,
                    "primitive-compile-0.1.0/Dockerfile": b"FROM scratch\n",
                    "primitive-compile-0.1.0/.github/workflows/ci.yaml": b"",
                }
            ),
        )

    monkeypatch.setattr(httpx, "get", get)
    release = PrimitiveRelease(
        "compile", FIRST, GitHubSource("uniconhq/primitive-compile", "v0.1.0")
    )

    files = primitives.source_files(release)

    assert asked == [
        "https://codeload.github.com/uniconhq/primitive-compile/tar.gz/refs/tags/v0.1.0"
    ]
    assert set(files) == {"primitive.yaml", "Dockerfile"}


def test_an_archive_naming_a_path_outside_the_repository_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _archive({"top/../../etc/passwd": b"x"})
    monkeypatch.setattr(
        httpx, "get", lambda url, **_: httpx.Response(200, content=content)
    )
    release = PrimitiveRelease("compile", FIRST, GitHubSource("o/r", "v1"))

    with pytest.raises(PrimitiveError, match="not a path inside"):
        primitives.source_files(release)
