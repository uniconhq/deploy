"""On a development stack bootstrap puts every image a grading step may run
on this machine, since the socket filter refuses a pull: each image the filter
is given, pulled only when the daemon does not hold it yet.
"""

from __future__ import annotations

from pathlib import Path

from bootstrap import main
from bootstrap.compose import Compose
from bootstrap.summary import Summary

COMPILE = "ghcr.io/uniconhq/primitive-compile@sha256:" + "1" * 64
RUN = "ghcr.io/uniconhq/primitive-sandbox-run@sha256:" + "2" * 64


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
