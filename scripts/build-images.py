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
writes images.local.json: the same shape as images.json, with every image a
localhost:5000 reference by digest and every primitive's source its sibling
checkout. That file is git-ignored, and bootstrap reads it instead of
images.json whenever it is there. Run it from the deploy directory:

    uv run scripts/build-images.py
    uv run bootstrap

A checkout is built as it is on disk, committed or not, so a change in a
sibling is one build and one bootstrap away from a grading. An image whose
build did not change keeps its digest, and bootstrap then changes nothing at
the forge; one that did change gets a new digest, and bootstrap makes the
primitive's next version (or rewrites v1, with --rewrite-v1). That is why the
build attaches no provenance or SBOM attestation: each carries the time of the
build, so every build would be a new digest even when nothing changed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

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
        _ensure_registry()
        built = {
            name: _build(
                name,
                context=siblings / "runner",
                dockerfile=siblings / "runner" / "images" / name / "Dockerfile",
            )
            for name in RUNNER_IMAGES
        }
        primitives: dict[str, dict[str, object]] = {}
        for name in PRIMITIVES:
            checkout = siblings / f"primitive-{name}"
            primitives[name] = {
                "image": _build(
                    f"primitive-{name}",
                    context=checkout,
                    dockerfile=checkout / "Dockerfile",
                ),
                "source": {"path": _relative(checkout, directory)},
            }
    except BuildFailed as failure:
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
    for name, entry in primitives.items():
        print(f"  {'primitive-' + name:<22} {entry['image']}")
    return 0


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
