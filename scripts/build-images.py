"""Build every image the stack grades with from the sibling checkouts, and
write the local image manifest bootstrap reads.

A plan names every image by digest, and a digest is what a registry says an
image is, so a development machine needs a registry to get real ones. This
starts one on this machine, `registry:2` on 127.0.0.1:5000, in a container of
its own outside the compose stack, with its images in the volume
`unicon-registry` so they survive a restart. Docker trusts a registry on
localhost over plain HTTP without being told, and the daemon pulls from it by
digest the same way it would from ghcr.io, which is what the CI agent and the
socket filter do. Nothing is pushed anywhere else.

It builds the runner's four images from ../runner (images/<name>/Dockerfile,
with the checkout as the context, the way the runner's own CI builds them)
and each primitive from ../primitive-<name> (its Dockerfile at the root),
pushes each to the registry, reads back the digest the push gave it, and
writes images.local.json: the same shape as images.json, with every runner
image a localhost:5000 reference by digest. Each primitive's checkout fills
the version at the forge that is the major of the version in its
pyproject.toml, `v2` for 2.0.0, with its source the sibling checkout; every
other version of it is copied from images.json as released, the image by its
digest at ghcr.io and the source its tag on GitHub. So a stack built this way
seeds v1 of each primitive from its release and v2 from the checkout. The
harness built here reads only a task's plans/plan.json, which a save on
unicon/classic@v2 writes, so a task saved on v1 grades only once it is saved
again on v2. images.local.json is git-ignored, and bootstrap reads it instead
of images.json whenever it is there. Run it from the deploy directory:

    uv run scripts/build-images.py
    uv run bootstrap

A checkout is built as it is on disk, committed or not, so a change in a
sibling is one build and one bootstrap away from a grading. An image whose
build did not change keeps its digest, and bootstrap then changes nothing at
the forge. One that did change gets a new digest, and bootstrap stops before
it seeds anything, naming the version at the forge that differs, since a
version is never edited; `uv run bootstrap --rewrite` rewrites it in place on
a development stack. That is why the build attaches no provenance or SBOM
attestation: each carries the time of the build, so every build would be a
new digest even when nothing changed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

import httpx

from bootstrap import images

RUNNER_IMAGES = ("harness", "clone", "socket-filter", "worker")
PRIMITIVES = ("compile", "sandbox-run", "diff-check")

REGISTRY = "localhost:5000"
REGISTRY_CONTAINER = "unicon-registry"
REGISTRY_IMAGE = "registry:2.8.3"
REGISTRY_VOLUME = "unicon-registry"
NAMESPACE = "uniconhq"
TAG = "dev"

_PUSHED_DIGEST = re.compile(r"digest: (sha256:[0-9a-f]{64})")


class BuildFailed(Exception):
    """A docker command this needs did not succeed."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the grading images locally and write images.local.json."
    )
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    parser.add_argument(
        "--siblings",
        type=Path,
        default=None,
        help="where the runner and primitive checkouts are. Default: beside deploy",
    )
    options = parser.parse_args(argv)
    directory = options.directory.resolve()
    siblings = (options.siblings or directory.parent).resolve()

    try:
        released = _released_primitives(directory)
        _ensure_registry()
        built = {
            name: _build(
                name,
                context=siblings / "runner",
                dockerfile=siblings / "runner" / "images" / name / "Dockerfile",
            )
            for name in RUNNER_IMAGES
        }
        primitives: dict[str, dict[str, Any]] = {}
        for name in PRIMITIVES:
            checkout = siblings / f"primitive-{name}"
            version = _version_of(checkout)
            versions = dict(released.get(name, {}))
            versions[version] = {
                "image": _build(
                    f"primitive-{name}",
                    context=checkout,
                    dockerfile=checkout / "Dockerfile",
                ),
                "source": {"path": _relative(checkout, directory)},
            }
            primitives[name] = dict(
                sorted(versions.items(), key=lambda item: int(item[0][1:]))
            )
    except (BuildFailed, images.ManifestError) as failure:
        print(f"build-images failed: {failure}")
        return 1

    manifest = directory / images.LOCAL_MANIFEST
    manifest.write_text(
        json.dumps({"images": built, "primitives": primitives}, indent=2) + "\n",
        encoding="utf-8",
    )
    images.load(manifest)
    print(f"\nwrote {manifest}")
    for name, reference in built.items():
        print(f"  {name:<22} {reference}")
    for name, versions in primitives.items():
        for version, entry in versions.items():
            label = f"primitive-{name} {version}"
            print(f"  {label:<22} {entry['image']}")
    return 0


def _released_primitives(directory: Path) -> dict[str, dict[str, Any]]:
    """Every primitive version the release manifest pins, as its entries
    there: the image at ghcr.io by digest and the source its tag on GitHub.
    """
    path = directory / images.RELEASE_MANIFEST
    images.load(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(name): dict(versions) for name, versions in document["primitives"].items()
    }


def _version_of(checkout: Path) -> str:
    """The version at the forge a checkout fills: the major of the version
    its pyproject.toml gives, `v2` for 2.0.0.
    """
    pyproject = checkout / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        major = int(str(project["version"]).split(".")[0])
    except OSError, KeyError, ValueError, tomllib.TOMLDecodeError:
        raise BuildFailed(
            f"{pyproject} gives no version <major>.<minor>.<patch>"
        ) from None
    if major < 1:
        raise BuildFailed(
            f"{pyproject} gives the major {major}; versions at the forge start at v1"
        )
    return f"v{major}"


def _relative(checkout: Path, directory: Path) -> str:
    """The checkout as a path from the deploy directory, the way a local
    manifest names a primitive's source.
    """
    return Path(os.path.relpath(checkout, directory)).as_posix()


def _ensure_registry() -> None:
    """Start the local registry, or keep the one already running."""
    state = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", REGISTRY_CONTAINER],
        capture_output=True,
        text=True,
    )
    if state.returncode != 0:
        _docker(
            "run registry",
            "run",
            "--detach",
            "--name",
            REGISTRY_CONTAINER,
            "--restart",
            "unless-stopped",
            "--publish",
            f"127.0.0.1:{REGISTRY.split(':')[1]}:5000",
            "--volume",
            f"{REGISTRY_VOLUME}:/var/lib/registry",
            REGISTRY_IMAGE,
        )
    elif state.stdout.strip() != "true":
        _docker("start registry", "start", REGISTRY_CONTAINER)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://{REGISTRY}/v2/", timeout=5.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise BuildFailed(f"the registry on {REGISTRY} did not answer within 60s")


def _build(name: str, *, context: Path, dockerfile: Path) -> str:
    """Build, push and return the image as a reference by digest."""
    if not dockerfile.is_file():
        raise BuildFailed(f"{dockerfile} is missing; is the checkout beside deploy?")
    repository = f"{REGISTRY}/{NAMESPACE}/{name}"
    print(f"building {repository}")
    _docker(
        f"build {name}",
        "build",
        "--provenance=false",
        "--sbom=false",
        "--file",
        str(dockerfile),
        "--tag",
        f"{repository}:{TAG}",
        str(context),
        quiet=True,
    )
    pushed = _docker(f"push {name}", "push", f"{repository}:{TAG}", quiet=True)
    digest = _PUSHED_DIGEST.search(pushed)
    if digest is None:
        raise BuildFailed(f"the push of {name} printed no digest")
    reference = f"{repository}@{digest.group(1)}"
    if not images.is_by_digest(reference):
        raise BuildFailed(f"{reference} is not a reference by digest")
    return reference


def _docker(description: str, *arguments: str, quiet: bool = False) -> str:
    """Run one docker command; its output is shown only when it fails."""
    completed = subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        sys.stdout.write(completed.stdout[-4000:])
        sys.stdout.write(completed.stderr[-4000:])
        raise BuildFailed(f"docker {description} exited {completed.returncode}")
    if not quiet:
        sys.stdout.write(completed.stdout)
    return completed.stdout


if __name__ == "__main__":
    sys.exit(main())
