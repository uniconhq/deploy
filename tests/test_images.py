"""The image manifest: which one a run reads, the shape it has to have, and
what bootstrap takes from it for .env.

Every image is a reference by digest, because a tag can be moved under a
running stack and a digest cannot; a manifest that names a tag is refused
before anything starts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bootstrap import images, main
from bootstrap.images import GitHubSource, LocalSource, ManifestError

DEPLOY = Path(__file__).resolve().parent.parent
DIGEST = "@sha256:" + "a" * 64


def _write(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _runner(registry: str = "ghcr.io/uniconhq") -> dict[str, str]:
    return {
        name: f"{registry}/{name}{DIGEST}"
        for name in ("harness", "clone", "socket-filter", "worker")
    }


def test_the_release_manifest_in_this_repo_loads() -> None:
    manifest = images.load(DEPLOY / images.RELEASE_MANIFEST)

    assert manifest.harness.startswith("ghcr.io/uniconhq/harness@sha256:")
    assert manifest.clone.startswith("ghcr.io/uniconhq/clone@sha256:")
    assert manifest.socket_filter.startswith("ghcr.io/uniconhq/socket-filter@sha256:")


def test_both_kinds_of_primitive_source_are_read(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "images.local.json",
        {
            "images": _runner("localhost:5000/uniconhq"),
            "primitives": {
                "sandbox-run": {
                    "image": f"ghcr.io/uniconhq/primitive-sandbox-run{DIGEST}",
                    "source": {
                        "github": "uniconhq/primitive-sandbox-run",
                        "tag": "v1.0.0",
                    },
                },
                "compile": {
                    "image": f"localhost:5000/uniconhq/primitive-compile{DIGEST}",
                    "source": {"path": "../primitive-compile"},
                },
            },
        },
    )

    manifest = images.load(path)

    assert [release.name for release in manifest.primitives] == [
        "compile",
        "sandbox-run",
    ]
    compile_, sandbox_run = manifest.primitives
    assert compile_.source == LocalSource(
        (tmp_path.parent / "primitive-compile").resolve()
    )
    assert sandbox_run.source == GitHubSource(
        "uniconhq/primitive-sandbox-run", "v1.0.0"
    )


@pytest.mark.parametrize(
    "reference",
    [
        "ghcr.io/uniconhq/harness:v0.1.0",
        "ghcr.io/uniconhq/harness@sha256:abc",
        "harness@sha256:" + "a" * 64,
        "",
        None,
    ],
)
def test_an_image_not_named_by_digest_is_refused(
    tmp_path: Path, reference: Any
) -> None:
    path = _write(
        tmp_path / "images.json",
        {"images": {**_runner(), "harness": reference}, "primitives": {}},
    )

    with pytest.raises(ManifestError, match=r"images\.harness is not an image"):
        images.load(path)


def test_a_manifest_without_the_clone_image_is_refused(tmp_path: Path) -> None:
    runner = _runner()
    del runner["clone"]
    path = _write(tmp_path / "images.json", {"images": runner, "primitives": {}})

    with pytest.raises(ManifestError, match="has no clone"):
        images.load(path)


def test_a_primitive_source_of_another_shape_is_refused(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "images.json",
        {
            "images": _runner(),
            "primitives": {
                "compile": {
                    "image": f"ghcr.io/uniconhq/primitive-compile{DIGEST}",
                    "source": {"github": "uniconhq/primitive-compile"},
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


def test_env_gets_the_manifest_images() -> None:
    manifest = images.load(DEPLOY / images.RELEASE_MANIFEST)
    values = {
        "UNICON_DB_PASSWORD": "x",
        "FORGEJO_PUBLIC_URL": "http://f.localhost",
        "UNICON_SESSION_HARD_TTL": "2592000",
    }

    main._derive_values(values, manifest, development=False)

    assert values["UNICON_HARNESS_IMAGE"] == manifest.harness
    assert values["UNICON_CLONE_IMAGE"] == manifest.clone
    assert values["UNICON_FILTER_IMAGE"] == manifest.socket_filter
