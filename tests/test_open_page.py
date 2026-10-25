"""A page open is tried again when the browser aborts it for the network."""

from __future__ import annotations

from typing import cast

import pytest
from playwright.sync_api import Error, Page

from tests.e2e.stack import open_page, reload_page


class _Page:
    def __init__(self, failures: int, message: str) -> None:
        self.failures = failures
        self.message = message
        self.opened: list[str] = []
        self.reloaded = 0

    def goto(self, url: str) -> None:
        self.opened.append(url)
        self._fail()

    def reload(self) -> None:
        self.reloaded += 1
        self._fail()

    def _fail(self) -> None:
        if self.failures == 0:
            return
        self.failures -= 1
        raise Error(self.message)


def test_opens_again_when_the_browser_reports_the_network_changed() -> None:
    page = _Page(1, "Page.goto: net::ERR_NETWORK_CHANGED at http://localhost")

    open_page(cast(Page, page), "http://localhost/tasks")

    assert page.opened == ["http://localhost/tasks", "http://localhost/tasks"]


def test_reloads_again_when_the_browser_reports_the_network_changed() -> None:
    page = _Page(1, "Page.reload: net::ERR_NETWORK_CHANGED")

    reload_page(cast(Page, page))

    assert page.reloaded == 2


def test_a_different_browser_failure_stands() -> None:
    page = _Page(1, "Page.goto: net::ERR_CONNECTION_REFUSED at http://localhost")

    with pytest.raises(Error, match="ERR_CONNECTION_REFUSED"):
        open_page(cast(Page, page), "http://localhost/tasks")

    assert page.opened == ["http://localhost/tasks"]


def test_the_network_change_stands_after_three_tries() -> None:
    page = _Page(3, "Page.goto: net::ERR_NETWORK_CHANGED at http://localhost")

    with pytest.raises(Error, match="ERR_NETWORK_CHANGED"):
        open_page(cast(Page, page), "http://localhost/tasks")

    assert len(page.opened) == 3
