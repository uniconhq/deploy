"""On a development stack bootstrap puts every image a grading step may run
on this machine, since the socket filter refuses a pull: each image the filter
is given, pulled only when the daemon does not hold it yet. The filter is
given the image of every version at the forge, so a stack that seeds v1 of a
primitive from its release and v2 from a local build pulls both.
"""

from __future__ import annotations

from pathlib import Path

from bootstrap import main, primitives
from bootstrap.compose import Compose
from bootstrap.forgejo import Forgejo
from bootstrap.platform_repos import PlatformRepos
from bootstrap.summary import Summary
from tests.forgejo_fake import FakeForgejo

COMPILE = "ghcr.io/uniconhq/primitive-compile@sha256:" + "1" * 64
RUN = "ghcr.io/uniconhq/primitive-sandbox-run@sha256:" + "2" * 64
LOCAL = "localhost:5000/uniconhq/primitive-compile@sha256:" + "3" * 64


class Daemon(Compose):
    def __init__(self, held: set[str]) -> None:
        super().__init__(Path("."), [])
        self.held = held
        self.pulled: list[str] = []

    def ensure_image(self, reference: str) -> bool:
        if reference in self.held:
            return False
        self.held.add(reference)
        self.pulled.append(reference)
        return True


def test_every_image_the_filter_allows_is_pulled_once() -> None:
    daemon = Daemon({COMPILE})
    values = {"UNICON_FILTER_IMAGES": f"{COMPILE},{RUN}"}

    first = Summary()
    main._pull_grading_images(daemon, values, first)
    again = Summary()
    main._pull_grading_images(daemon, values, again)

    assert daemon.pulled == [RUN]
    assert first.lines()[0] == "created: 1, already present: 1"
    assert again.lines()[0] == "created: 0, already present: 2"


def test_no_filter_images_pulls_nothing() -> None:
    daemon = Daemon(set())

    main._pull_grading_images(daemon, {"UNICON_FILTER_IMAGES": ""}, Summary())

    assert daemon.pulled == []


def test_the_image_of_every_version_at_the_forge_is_pulled() -> None:
    forge = FakeForgejo()
    released = f"name: unicon/compile\nversion: v1\nimage: {COMPILE}\n"
    repo = forge.seed(
        "unicon/compile.primitive",
        {"primitive.yaml": released.encode()},
        tags=("v1",),
    )
    repo.tags["v2"] = repo.commit({"primitive.yaml": f"image: {LOCAL}\n".encode()})
    repos = PlatformRepos(Forgejo(forge, "forgejo"), "token")
    values = {
        "UNICON_FILTER_IMAGES": ",".join(primitives.images_at_forge(repos, ["compile"]))
    }
    daemon = Daemon(set())

    main._pull_grading_images(daemon, values, Summary())

    assert daemon.pulled == [COMPILE, LOCAL]
