"""The image manifest: which one a run reads, the shape it has to have, and
what bootstrap takes from it for .env.

Every image is a reference by digest, because a tag can be moved under a
running stack and a digest cannot; a manifest that names a tag is refused
before anything starts. A primitive's entries are keyed by its version at the
forge, which is the major of its release tag.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bootstrap import images
from bootstrap.images import GitHubSource, LocalSource, ManifestError

DEPLOY = Path(__file__).resolve().parent.parent
DIGEST = "@sha256:" + "a" * 64


def _write(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _runner(registry: str = "ghcr.io/uniconhq") -> dict[str, str]:
    return {"step-helper": f"{registry}/step-helper{DIGEST}"}


def test_the_release_manifest_loads_with_no_step_helper_yet() -> None:
    """No step helper is released, so the release manifest loads, and only
    writing labels from it is refused."""
    manifest = images.load(DEPLOY / images.RELEASE_MANIFEST)

    with pytest.raises(ManifestError, match="has no step-helper"):
        _ = manifest.step_helper
    assert manifest.primitive_names == ["exec"]
    for release in manifest.primitives:
        assert isinstance(release.source, GitHubSource)
        assert release.image.startswith(
            f"ghcr.io/uniconhq/primitive-{release.name}@sha256:"
        )
        assert release.source.repo == f"uniconhq/primitive-{release.name}"
        assert release.version == f"v{images.major_of(release.source.tag)}"


def test_both_kinds_of_primitive_source_are_read(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "images.local.json",
        {
            "images": _runner("localhost:5000/uniconhq"),
            "primitives": {
                "exec": {
                    "v3": {
                        "image": f"localhost:5000/uniconhq/primitive-exec{DIGEST}",
                        "source": {"path": "../primitive-exec"},
                    },
                    "v2": {
                        "image": f"ghcr.io/uniconhq/primitive-exec{DIGEST}",
                        "source": {
                            "github": "uniconhq/primitive-exec",
                            "tag": "v2.0.0",
                        },
                    },
                },
                "check": {
                    "v1": {
                        "image": f"ghcr.io/uniconhq/primitive-check{DIGEST}",
                        "source": {
                            "github": "uniconhq/primitive-check",
                            "tag": "v1.0.0",
                        },
                    },
                },
            },
        },
    )

    manifest = images.load(path)

    assert [(release.name, release.version) for release in manifest.primitives] == [
        ("check", "v1"),
        ("exec", "v2"),
        ("exec", "v3"),
    ]
    check_v1, exec_v2, exec_v3 = manifest.primitives
    assert check_v1.source == GitHubSource("uniconhq/primitive-check", "v1.0.0")
    assert exec_v2.source == GitHubSource("uniconhq/primitive-exec", "v2.0.0")
    assert exec_v3.source == LocalSource((tmp_path.parent / "primitive-exec").resolve())
    assert manifest.primitive_names == ["check", "exec"]
    assert manifest.versions_of("exec") == [exec_v2, exec_v3]


def test_versions_are_in_number_order(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "images.json",
        {
            "images": _runner(),
            "primitives": {
                "exec": {
                    version: {
                        "image": f"ghcr.io/uniconhq/primitive-exec{DIGEST}",
                        "source": {"path": f"../exec-{version}"},
                    }
                    for version in ("v10", "v2", "v1")
                }
            },
        },
    )

    assert [release.version for release in images.load(path).primitives] == [
        "v1",
        "v2",
        "v10",
    ]


def _exec_versions(versions: Any) -> dict[str, Any]:
    return {"images": _runner(), "primitives": {"exec": versions}}


def _released(tag: str) -> dict[str, Any]:
    return {
        "image": f"ghcr.io/uniconhq/primitive-exec{DIGEST}",
        "source": {"github": "uniconhq/primitive-exec", "tag": tag},
    }


def test_a_release_under_another_major_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "images.json", _exec_versions({"v1": _released("v2.0.0")}))

    with pytest.raises(ManifestError, match=r"v2\.0\.0, whose major is 2") as refused:
        images.load(path)

    assert "belongs under v2" in str(refused.value)


def test_a_tag_that_is_not_a_release_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "images.json", _exec_versions({"v1": _released("latest")}))

    with pytest.raises(ManifestError, match="is not a release tag"):
        images.load(path)


@pytest.mark.parametrize(
    "versions",
    [
        # The shape before versions: one image and source per primitive.
        _released("v1.1.1"),
        {"1": _released("v1.1.1")},
        {"v0": _released("v0.1.0")},
    ],
)
def test_entries_not_keyed_by_version_are_refused(
    tmp_path: Path, versions: Any
) -> None:
    path = _write(tmp_path / "images.json", _exec_versions(versions))

    with pytest.raises(ManifestError, match="is not a version"):
        images.load(path)


def test_a_primitive_with_no_version_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "images.json", _exec_versions({}))

    with pytest.raises(ManifestError, match="not an object of versions"):
        images.load(path)


@pytest.mark.parametrize(
    "reference",
    [
        "ghcr.io/uniconhq/step-helper:v0.1.0",
        "ghcr.io/uniconhq/step-helper@sha256:abc",
        "step-helper@sha256:" + "a" * 64,
        "",
        None,
    ],
)
def test_an_image_not_named_by_digest_is_refused(
    tmp_path: Path, reference: Any
) -> None:
    path = _write(
        tmp_path / "images.json",
        {"images": {"step-helper": reference}, "primitives": {}},
    )

    with pytest.raises(ManifestError, match=r"images\.step-helper is not an image"):
        images.load(path)


def test_a_manifest_names_its_step_helper(tmp_path: Path) -> None:
    path = _write(tmp_path / "images.json", {"images": _runner(), "primitives": {}})

    assert images.load(path).step_helper == f"ghcr.io/uniconhq/step-helper{DIGEST}"


def test_a_primitive_source_of_another_shape_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "images.json",
        {
            "images": _runner(),
            "primitives": {
                "exec": {
                    "v1": {
                        "image": f"ghcr.io/uniconhq/primitive-exec{DIGEST}",
                        "source": {"github": "uniconhq/primitive-exec"},
                    }
                }
            },
        },
    )

    with pytest.raises(ManifestError, match="neither"):
        images.load(path)


def test_a_missing_manifest_is_named(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="missing"):
        images.load(tmp_path / "images.json")


def test_the_local_manifest_is_read_when_a_build_wrote_one(tmp_path: Path) -> None:
    assert images.choose(tmp_path, None) == tmp_path / "images.json"
    (tmp_path / "images.local.json").write_text("{}")
    assert images.choose(tmp_path, None) == tmp_path / "images.local.json"
    assert images.choose(tmp_path, Path("other.json")) == tmp_path / "other.json"
