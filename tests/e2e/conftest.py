from __future__ import annotations

from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, sync_playwright


@pytest.fixture
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        try:
            yield launched
        finally:
            launched.close()
