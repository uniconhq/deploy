"""Build every image the stack grades with from the sibling checkouts, and
write the local image manifest bootstrap reads.

A plan names every image by digest, and a digest is what a registry says an
image is, so a development machine needs a registry to get real ones. This
starts one on this machine, `registry:2` on 127.0.0.1:5000, in a container of
its own outside the compose stack, with its images in the volume
`unicon-registry` so they survive a restart. Docker trusts a registry on
localhost over plain HTTP without being told, and the daemon pulls from it by
digest the same way it would from ghcr.io, which is what the CI agent does.
Nothing is pushed anywhere else.

It builds the runner's two images from ../runner (images/<name>/Dockerfile,
with the checkout as the context, the way the runner's own CI builds them),
primitive-base first and then the worker, and each primitive from
../primitive-<name> (its Dockerfile at the root) on that primitive-base,
passed as the build argument BASE_IMAGE by its digest here, pushes each to
the registry, reads back the digest the push gave it, and writes
images.local.json: the same shape as images.json, with every runner
image a localhost:5000 reference by digest, and every other image there,
Woodpecker's git plugin, copied from images.json as released. Each
primitive's checkout fills the version at the forge that is the major of
the version in its pyproject.toml, `v2` for 2.0.0, with its source the
sibling checkout; every other version of it is copied from images.json as
released, the image by its digest at ghcr.io and the source its tag on
GitHub. So a stack built this way seeds compile's v1 from its release and
its v3 from the checkout. images.local.json is git-ignored, and bootstrap
reads it instead of images.json whenever it is there. Run it from the deploy directory:

    uv run scripts/build-images.py
    uv run bootstrap

The CI builds through a buildx builder of its own, `--builder NAME
--gha-cache`, which reads each image's layers from the GitHub Actions cache
and writes them back, under a scope per image, so a layer whose inputs did
not change since an earlier run is not built again. The image is loaded into
Docker and pushed from there, as a laptop's build is, so its digest is read
the same way and Docker holds the image, which bootstrap then finds without
a pull. The primitives, on primitive-base, are built by Docker's own builder
whatever the options, since a buildx builder of its own runs in a container
of its own, which reaches neither the images Docker holds nor the registry
on this machine's localhost.

A checkout is built as it is on disk, committed or not, so a change in a
sibling is one build and one bootstrap away from a grading. An image whose
build did not change keeps its digest, and bootstrap then changes nothing at
the forge. One that did change gets a new digest, and bootstrap moves the
version at the forge to it when its declaration is otherwise the same, as
for a patch release; a changed declaration stops bootstrap before it seeds
anything, naming the version that differs, and `uv run bootstrap --rewrite`
rewrites it in place on a development stack. That is why the build attaches
no provenance or SBOM attestation: each carries the time of the build, so
every build would be a new digest even when nothing changed.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from bootstrap import images

RUNNER_IMAGES = ("worker",)
PRIMITIVES = ("compile", "exec")
"""The primitives, each built on the runner's `images.PRIMITIVE_BASE`, which
is built first.
"""

REGISTRY = "localhost:5000"
REGISTRY_ASKED = "127.0.0.1:5000"
"""Where this script asks whether the registry answers: the address it is
published on, not `localhost`, which may name ::1 first, where macOS's
AirPlay Receiver listens on port 5000 and answers 403. The daemon pushes and
pulls through `REGISTRY` inside its own machine, where no such listener is.
"""
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
    parser.add_argument(
        "--builder",
        default=None,
        help="the buildx builder to build with, the image loaded into Docker "
        "from it before the push. Default: Docker's own",
    )
    parser.add_argument(
        "--gha-cache",
        action="store_true",
        help="read and write each image's layers in the GitHub Actions cache; "
        "needs --builder and the Actions runtime's variables",
    )
    options = parser.parse_args(argv)
    if options.gha_cache and options.builder is None:
        parser.error("--gha-cache needs --builder: Docker's own cannot write to it")
    directory = options.directory.resolve()
    siblings = (options.siblings or directory.parent).resolve()
    build = Build(builder=options.builder, gha_cache=options.gha_cache)

    try:
        released = _released_primitives(directory)
        _ensure_registry()
        built = {
            name: build.image(
                name,
                context=siblings / "runner",
                dockerfile=siblings / "runner" / "images" / name / "Dockerfile",
            )
            for name in (images.PRIMITIVE_BASE, *RUNNER_IMAGES)
        }
        on_base = Build()
        primitives: dict[str, dict[str, Any]] = {}
        for name in PRIMITIVES:
            checkout = siblings / f"primitive-{name}"
            version = _version_of(checkout)
            versions = dict(released.get(name, {}))
            versions[version] = {
                "image": on_base.image(
                    f"primitive-{name}",
                    context=checkout,
                    dockerfile=checkout / "Dockerfile",
                    arguments={"BASE_IMAGE": built[images.PRIMITIVE_BASE]},
                ),
                "source": {"path": _relative(checkout, directory)},
            }
            primitives[name] = dict(
                sorted(versions.items(), key=lambda item: int(item[0][1:]))
            )
    except (BuildFailed, images.ManifestError) as failure:
        print(f"build-images failed: {failure}")
        return 1

    built = {**_released_images(directory, built), **built}
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


def _released_images(directory: Path, built: dict[str, str]) -> dict[str, str]:
    """Every image the release manifest pins that this does not build, such
    as Woodpecker's git plugin, by its digest there."""
    document = json.loads(
        (directory / images.RELEASE_MANIFEST).read_text(encoding="utf-8")
    )
    return {
        str(name): str(reference)
        for name, reference in document["images"].items()
        if name not in built
    }


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
            f"{REGISTRY_ASKED}:5000",
            "--volume",
            f"{REGISTRY_VOLUME}:/var/lib/registry",
            REGISTRY_IMAGE,
        )
    elif state.stdout.strip() != "true":
        _docker("start registry", "start", REGISTRY_CONTAINER)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            answer = httpx.get(f"http://{REGISTRY_ASKED}/v2/", timeout=5.0)
            if answer.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise BuildFailed(f"the registry on {REGISTRY} did not answer within 60s")


@dataclass(frozen=True)
class Build:
    """How an image is built: by Docker's own builder, or by a buildx builder
    of `builder`'s name, loading the image into Docker, and with
    `gha_cache` reading and writing its layers in the GitHub Actions cache.
    """

    builder: str | None = None
    gha_cache: bool = False

    def image(
        self,
        name: str,
        *,
        context: Path,
        dockerfile: Path,
        arguments: dict[str, str] | None = None,
    ) -> str:
        """Build, push and return the image as a reference by digest, with
        `arguments` as its build arguments."""
        if not dockerfile.is_file():
            raise BuildFailed(
                f"{dockerfile} is missing; is the checkout beside deploy?"
            )
        repository = f"{REGISTRY}/{NAMESPACE}/{name}"
        print(f"building {repository}")
        _docker(
            f"build {name}",
            *self.command(name),
            "--provenance=false",
            "--sbom=false",
            *[
                flag
                for key, value in (arguments or {}).items()
                for flag in ("--build-arg", f"{key}={value}")
            ],
            "--file",
            str(dockerfile),
            "--tag",
            f"{repository}:{TAG}",
            str(context),
            quiet=True,
        )
        return _push(name, repository)

    def command(self, name: str) -> list[str]:
        """The docker command and the options that choose the builder and
        the cache, ahead of the ones every build has.
        """
        if self.builder is None:
            return ["build"]
        command = ["buildx", "build", "--builder", self.builder, "--load"]
        if self.gha_cache:
            # A scope per image, since a scope holds one build's cache and
            # each write replaces the last. A failed write costs the next
            # run its reuse and nothing else, so it does not fail the build.
            scope = f"scope=build-images-{name}"
            command += [
                "--cache-from",
                f"type=gha,{scope}",
                "--cache-to",
                f"type=gha,{scope},mode=max,ignore-error=true",
            ]
        return command


def _push(name: str, repository: str) -> str:
    """Push the image built as `repository`'s dev tag and return it as a
    reference by the digest the registry gave it.
    """
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
