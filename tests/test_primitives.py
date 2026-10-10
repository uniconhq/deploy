"""Mirroring the primitives into the platform org: each version the manifest
lists goes to its own tag with the manifest's image in its declaration, a
rerun with nothing changed makes nothing, and a tag already at the forge is
never edited, whatever the manifest says now.

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
FIRST = "ghcr.io/uniconhq/primitive-compile@sha256:" + "1" * 64
SECOND = "localhost:5000/uniconhq/primitive-compile@sha256:" + "2" * 64
OTHER = "ghcr.io/uniconhq/primitive-compile@sha256:" + "3" * 64
RUNNER = "@sha256:" + "0" * 64
# A v1 release's declaration names the primitive and its version.
NAMED = b"""# The compile primitive.
name: unicon/compile
version: v1
batch: false
inputs:
  source: {type: file}
"""
# A v2 release's declaration names neither: the repository and tag do.
UNNAMED = b"""# The compile primitive.
batch: false
inputs:
  source: {type: folder, runs: true}
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
    """A checkout of the compile primitive at a v1 release."""
    return _checkout(
        tmp_path / "primitive-compile",
        {
            "primitive.yaml": NAMED,
            "Dockerfile": b"FROM scratch\n",
            "compile.py": b"print('compile')\n",
            **extra,
        },
    )


def _compile_v2(tmp_path: Path) -> Path:
    """A checkout of the compile primitive at a v2 release."""
    return _checkout(
        tmp_path / "primitive-compile-v2",
        {
            "primitive.yaml": UNNAMED,
            "Dockerfile": b"FROM scratch\n",
            "compile.py": b"print('compile v2')\n",
        },
    )


def _manifest(*versions: tuple[str, Path, str]) -> Manifest:
    """A manifest pinning these versions of compile: (version, checkout,
    image) each.
    """
    return Manifest(
        path=DEPLOY / "images.local.json",
        images={
            name: f"localhost:5000/uniconhq/{name}{RUNNER}"
            for name in ("harness", "clone", "socket-filter")
        },
        primitives=tuple(
            PrimitiveRelease("compile", version, image, LocalSource(checkout))
            for version, checkout, image in versions
        ),
    )


def _seed(
    forge: FakeForgejo, manifest: Manifest, rewrite: bool = False
) -> tuple[Summary, dict[str, str]]:
    summary = Summary()
    values = {"UNICON_FORGE_ADMIN_TOKEN": TOKEN}
    main._seed_platform(forge, DEPLOY, manifest, values, summary, rewrite)
    return summary, values


def test_a_fresh_forge_gets_v1_then_v2(tmp_path: Path) -> None:
    forge = FakeForgejo()
    manifest = _manifest(
        ("v1", _compile(tmp_path), FIRST), ("v2", _compile_v2(tmp_path), SECOND)
    )

    summary, values = _seed(forge, manifest)

    repo = forge.repos[FULL]
    assert not repo.private
    assert repo.topics == ["unicon-primitive"]
    assert list(repo.tags) == ["v1", "v2"]
    assert forge.writes()[2:6] == [
        ("POST", f"/repos/{FULL}/contents"),
        ("POST", f"/repos/{FULL}/tags"),
        ("POST", f"/repos/{FULL}/contents"),
        ("POST", f"/repos/{FULL}/tags"),
    ]
    v1 = repo.files_at("v1")
    assert set(v1) == {"primitive.yaml", "Dockerfile", "compile.py"}
    assert v1["primitive.yaml"].decode().splitlines()[:4] == [
        "# The compile primitive.",
        "name: unicon/compile",
        "version: v1",
        f"image: {FIRST}",
    ]
    v2 = repo.files_at("v2")
    assert v2["compile.py"] == b"print('compile v2')\n"
    assert v2["primitive.yaml"] == f"image: {SECOND}\n".encode() + UNNAMED
    assert "  created  primitive unicon/compile version v1" in summary.lines()
    assert "  created  primitive unicon/compile version v2" in summary.lines()
    assert values["UNICON_FILTER_IMAGES"] == f"{FIRST},{SECOND}"


def test_a_rerun_with_nothing_changed_makes_nothing(tmp_path: Path) -> None:
    forge = FakeForgejo()
    manifest = _manifest(
        ("v1", _compile(tmp_path), FIRST), ("v2", _compile_v2(tmp_path), SECOND)
    )
    _seed(forge, manifest)
    forge.requests.clear()

    summary, values = _seed(forge, manifest)

    assert forge.writes() == []
    assert summary.lines()[0].startswith("created: 0,")
    assert "  present  primitive unicon/compile version v1" in summary.lines()
    assert "  present  primitive unicon/compile version v2" in summary.lines()
    assert values["UNICON_FILTER_IMAGES"] == f"{FIRST},{SECOND}"


def test_a_v1_written_the_way_bootstrap_always_has_is_present(
    tmp_path: Path,
) -> None:
    """A stack seeded before versions were listed in the manifest holds v1
    with `version` and `image` under `name`; the same release reads as the
    same version.
    """
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    forge.seed(
        FULL,
        {
            "primitive.yaml": (
                b"# The compile primitive.\nname: unicon/compile\nversion: v1\n"
                + f"image: {FIRST}\n".encode()
                + b"batch: false\ninputs:\n  source: {type: file}\n"
            ),
            "Dockerfile": b"FROM scratch\n",
            "compile.py": b"print('compile')\n",
        },
        tags=("v1",),
        topics=("unicon-primitive",),
    )

    summary, _ = _seed(forge, _manifest(("v1", checkout, FIRST)))

    assert [write for write in forge.writes() if FULL in write[1]] == []
    assert "  present  primitive unicon/compile version v1" in summary.lines()


def test_a_new_image_alone_moves_the_version_in_place(tmp_path: Path) -> None:
    """A patch release, a fixed image with the same ports, moves its
    version's tag to a commit naming the new digest, with the release's
    other files. A task already published keeps the digest its plan pinned,
    so the old image stays on the socket filter's list.
    """
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(("v1", checkout, FIRST)))
    repo = forge.repos[FULL]
    before = repo.tags["v1"]
    (checkout / "compile.py").write_bytes(b"print('fixed')\n")

    summary, values = _seed(
        forge,
        _manifest(("v1", checkout, OTHER), ("v2", _compile_v2(tmp_path), SECOND)),
    )

    assert repo.tags["v1"] != before
    v1 = repo.files_at("v1")
    assert f"image: {OTHER}" in v1["primitive.yaml"].decode()
    assert f"image: {FIRST}" not in v1["primitive.yaml"].decode()
    assert v1["compile.py"] == b"print('fixed')\n"
    assert set(repo.files_at("v2")) == {"primitive.yaml", "Dockerfile", "compile.py"}
    assert "  created  primitive unicon/compile version v1 (moved)" in (summary.lines())
    assert "  created  primitive unicon/compile version v2" in summary.lines()
    assert values["UNICON_FILTER_IMAGES"] == f"{FIRST},{OTHER},{SECOND}"


def test_a_moved_version_is_present_on_the_next_run(tmp_path: Path) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(("v1", checkout, FIRST)))
    _seed(forge, _manifest(("v1", checkout, OTHER)))
    forge.requests.clear()

    summary, values = _seed(forge, _manifest(("v1", checkout, OTHER)))

    assert forge.writes() == []
    assert "  present  primitive unicon/compile version v1" in summary.lines()
    assert values["UNICON_FILTER_IMAGES"] == f"{FIRST},{OTHER}"


def test_a_new_image_with_other_ports_stops_the_run_before_anything_is_seeded(
    tmp_path: Path,
) -> None:
    """v2, new in the manifest, is not made either: the run stops before it
    writes anything, naming the version that differs and what to do.
    """
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(("v1", checkout, FIRST)))
    repo = forge.repos[FULL]
    tags = dict(repo.tags)
    head = repo.head
    (checkout / "primitive.yaml").write_bytes(
        NAMED + b"outputs:\n  binary: {type: file}\n"
    )
    forge.requests.clear()

    with pytest.raises(main.DifferingVersions) as refused:
        _seed(
            forge,
            _manifest(("v1", checkout, OTHER), ("v2", _compile_v2(tmp_path), SECOND)),
        )

    assert forge.writes() == []
    assert repo.tags == tags
    assert repo.head == head
    assert f"image: {FIRST}" in repo.files_at("v1")["primitive.yaml"].decode()
    message = str(refused.value)
    assert "unicon/compile@v1, against what images.local.json pins for it" in (message)
    assert "unicon/compile@v2" not in message
    assert "new version in images.local.json" in message
    assert "--rewrite" in message


def test_a_changed_declaration_stops_the_run_too(tmp_path: Path) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(("v1", checkout, FIRST)))
    (checkout / "primitive.yaml").write_bytes(NAMED + b"batch_note: x\n")
    forge.requests.clear()

    with pytest.raises(main.DifferingVersions, match="unicon/compile@v1"):
        _seed(forge, _manifest(("v1", checkout, FIRST)))

    assert forge.writes() == []
    assert list(forge.repos[FULL].tags) == ["v1"]


def test_an_older_format_under_a_released_version_stops_the_run(
    tmp_path: Path,
) -> None:
    """A stack that numbered each rebuilt image as the next version holds a
    v2 in the format of v1; the v2 release is not mirrored over it.
    """
    forge = FakeForgejo()
    forge.seed(
        FULL,
        {
            "primitive.yaml": (
                b"name: unicon/compile\nversion: v2\n"
                + f"image: {FIRST}\n".encode()
                + b"batch: false\n"
            ),
            "Dockerfile": b"FROM scratch\n",
        },
        tags=("v1", "v2"),
        topics=("unicon-primitive",),
    )
    forge.requests.clear()

    with pytest.raises(main.DifferingVersions, match="unicon/compile@v2"):
        _seed(forge, _manifest(("v2", _compile_v2(tmp_path), SECOND)))

    assert forge.writes() == []


def test_a_changed_program_alone_is_the_same_version(tmp_path: Path) -> None:
    """The program that runs is the one in the image, so without a new
    digest nothing that grades has changed.
    """
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(("v1", checkout, FIRST)))
    (checkout / "compile.py").write_bytes(b"print('changed')\n")
    forge.requests.clear()

    summary, _ = _seed(forge, _manifest(("v1", checkout, FIRST)))

    assert forge.writes() == []
    assert "  present  primitive unicon/compile version v1" in summary.lines()


def test_a_version_added_later_is_made_and_the_earlier_left(tmp_path: Path) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    _seed(forge, _manifest(("v1", checkout, FIRST)))
    repo = forge.repos[FULL]
    first = repo.tags["v1"]
    v1_files = repo.files_at("v1")

    summary, _ = _seed(
        forge, _manifest(("v1", checkout, FIRST), ("v2", _compile_v2(tmp_path), SECOND))
    )

    assert repo.tags["v1"] == first
    assert repo.files_at("v1") == v1_files
    assert set(repo.files_at("v2")) == {"primitive.yaml", "Dockerfile", "compile.py"}
    assert "  present  primitive unicon/compile version v1" in summary.lines()
    assert "  created  primitive unicon/compile version v2" in summary.lines()


def test_rewrite_on_a_development_stack_moves_the_version(tmp_path: Path) -> None:
    forge = FakeForgejo()
    checkout = _compile(tmp_path)
    v2 = _compile_v2(tmp_path)
    _seed(forge, _manifest(("v1", checkout, FIRST), ("v2", v2, SECOND)))
    repo = forge.repos[FULL]
    v1 = repo.tags["v1"]

    (v2 / "primitive.yaml").write_bytes(UNNAMED + b"  language: {type: text}\n")

    summary, _ = _seed(
        forge, _manifest(("v1", checkout, FIRST), ("v2", v2, OTHER)), rewrite=True
    )

    assert list(repo.tags) == ["v1", "v2"]
    assert repo.tags["v1"] == v1
    written = repo.files_at("v2")["primitive.yaml"].decode()
    assert f"image: {OTHER}" in written
    assert "language: {type: text}" in written
    assert (
        "  created  primitive unicon/compile version v2 (rewritten)" in summary.lines()
    )


def test_a_named_declaration_gets_version_and_image_under_its_name() -> None:
    source = (
        b"name: 'unicon/compile'  # the name\r\n"
        b"version: v9\r\nimage: ghcr.io/x/y:latest\r\nbatch: false\r\n"
    )

    written = primitives.declaration(source, "compile", "v1", FIRST).decode()

    assert written.split("\r\n") == [
        "name: 'unicon/compile'  # the name",
        "version: v1",
        f"image: {FIRST}",
        "batch: false",
        "",
    ]
    assert primitives.image_of(written.encode()) == FIRST


def test_an_unnamed_declaration_gets_the_image_as_its_first_line() -> None:
    source = (
        b"# The run primitive.\r\nimage: ghcr.io/x/y:latest\r\n"
        b"version: kept\r\nbatch: true\r\n"
    )

    written = primitives.declaration(source, "sandbox-run", "v2", SECOND).decode()

    assert written.split("\r\n") == [
        f"image: {SECOND}",
        "# The run primitive.",
        "version: kept",
        "batch: true",
        "",
    ]
    assert primitives.image_of(written.encode()) == SECOND


def test_a_declaration_for_another_primitive_is_refused() -> None:
    with pytest.raises(PrimitiveError, match="unicon/run"):
        primitives.declaration(b"name: unicon/run\n", "compile", "v1", FIRST)


@pytest.mark.parametrize(
    "source",
    [b"name: unicon/compile\nimage:\n  ghcr.io/x\n", b"image:\n  ghcr.io/x\n"],
)
def test_an_image_over_several_lines_is_refused(source: bytes) -> None:
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
    release = PrimitiveRelease("compile", "v1", FIRST, LocalSource(checkout))

    assert set(primitives.source_files(release)) == {
        ".gitignore",
        "primitive.yaml",
        "Dockerfile",
        "compile.py",
        "tests/test_compile.py",
    }


def test_a_repository_without_a_dockerfile_is_refused(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path / "p", {"primitive.yaml": NAMED})

    with pytest.raises(PrimitiveError, match="Dockerfile"):
        primitives.source_files(
            PrimitiveRelease("compile", "v1", FIRST, LocalSource(checkout))
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
                    "primitive-compile-1.0.0/primitive.yaml": NAMED,
                    "primitive-compile-1.0.0/Dockerfile": b"FROM scratch\n",
                    "primitive-compile-1.0.0/.github/workflows/ci.yaml": b"",
                }
            ),
        )

    monkeypatch.setattr(httpx, "get", get)
    release = PrimitiveRelease(
        "compile", "v1", FIRST, GitHubSource("uniconhq/primitive-compile", "v1.0.0")
    )

    files = primitives.source_files(release)

    assert asked == [
        "https://codeload.github.com/uniconhq/primitive-compile/tar.gz/refs/tags/v1.0.0"
    ]
    assert set(files) == {"primitive.yaml", "Dockerfile"}


def test_an_archive_naming_a_path_outside_the_repository_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = _archive({"top/../../etc/passwd": b"x"})
    monkeypatch.setattr(
        httpx, "get", lambda url, **_: httpx.Response(200, content=content)
    )
    release = PrimitiveRelease("compile", "v1", FIRST, GitHubSource("o/r", "v1.0.0"))

    with pytest.raises(PrimitiveError, match="not a path inside"):
        primitives.source_files(release)
