"""Bootstrap hands every running service an `up` after a run and lets
compose decide whether its configuration changed; one that is not running
is not started.
"""

from __future__ import annotations

import pytest

from bootstrap import main


class _Compose:
    """Containers by service, and the services whose configuration differs
    from the one their container was made with.
    """

    def __init__(self, running: dict[str, str], changed: set[str]) -> None:
        self.running = running
        self.changed = changed
        self.brought_up: list[str] = []

    def is_running(self, service: str) -> bool:
        return service in self.running

    def container_id(self, service: str) -> str | None:
        return self.running.get(service)

    def up(self, service: str) -> None:
        self.brought_up.append(service)
        if service in self.changed:
            self.running[service] += "-new"


def test_compose_decides_what_is_recreated(capsys: pytest.CaptureFixture[str]) -> None:
    compose = _Compose({"backend": "b", "socket-filter": "f"}, changed={"backend"})

    main.recreate_changed(compose, "backend")  # type: ignore[arg-type]
    main.recreate_changed(compose, *main.GRADING_SERVICES)  # type: ignore[arg-type]

    assert compose.brought_up == ["backend", "socket-filter"]
    assert capsys.readouterr().out.splitlines() == [
        "recreated backend, as its configuration changed"
    ]


def test_a_service_that_is_not_running_is_left_alone() -> None:
    compose = _Compose({}, changed={"backend", "woodpecker-agent"})

    main.recreate_changed(compose, "backend", *main.GRADING_SERVICES)  # type: ignore[arg-type]

    assert compose.brought_up == []
