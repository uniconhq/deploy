"""Waiting for a service to answer.

Docker Desktop on Windows takes seconds to start a container and Forgejo runs
its database migrations on first boot, so these waits are long on purpose.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx


class NotReady(Exception):
    """A service did not answer within its deadline."""


def wait_for(
    description: str,
    check: Callable[[], bool],
    timeout_seconds: float = 180.0,
    interval_seconds: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            if check():
                return
        except httpx.HTTPError, OSError:
            pass
        if time.monotonic() >= deadline:
            raise NotReady(
                f"{description} did not answer within {timeout_seconds:.0f}s"
            )
        time.sleep(interval_seconds)


def wait_for_http(
    description: str,
    url: str,
    timeout_seconds: float = 180.0,
    headers: dict[str, str] | None = None,
) -> None:
    def answered() -> bool:
        response = httpx.get(url, headers=headers, timeout=10.0)
        return response.status_code < 500

    wait_for(description, answered, timeout_seconds=timeout_seconds)
