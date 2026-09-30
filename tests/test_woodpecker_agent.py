"""Enrolling the development agent at the CI: an agent the server already
knows by name keeps its token, and a missing one is made as a global agent
through the administrator's API rather than copied by hand from the Agents
page. Afterwards every other agent that would take a grading run and has gone
silent is deleted, and one still reporting is left alone.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from bootstrap.woodpecker import Woodpecker, WoodpeckerError

URL = "http://localhost:8000"


class FakeCI:
    def __init__(self, agents: list[dict[str, Any]], create_status: int = 200) -> None:
        self.agents = agents
        self.create_status = create_status
        self.posted: list[Any] = []
        self.deleted: list[str] = []
        self.delete_status = 204
        self.headers: list[str] = []

    def get(self, url: str, **options: Any) -> httpx.Response:
        assert url == f"{URL}/api/agents"
        self.headers.append(options["headers"]["Authorization"])
        page, size = options["params"]["page"], options["params"]["perPage"]
        return httpx.Response(200, json=self.agents[(page - 1) * size : page * size])

    def post(self, url: str, **options: Any) -> httpx.Response:
        assert url == f"{URL}/api/agents"
        self.headers.append(options["headers"]["Authorization"])
        self.posted.append(options["json"])
        if self.create_status != 200:
            return httpx.Response(self.create_status, json={"message": "no"})
        return httpx.Response(200, json={"id": 9, "org_id": -1, "token": "new"})

    def delete(self, url: str, **options: Any) -> httpx.Response:
        assert url.startswith(f"{URL}/api/agents/")
        self.headers.append(options["headers"]["Authorization"])
        if self.delete_status < 300:
            self.deleted.append(url.rsplit("/", 1)[1])
        return httpx.Response(self.delete_status)


@pytest.fixture
def ci(monkeypatch: pytest.MonkeyPatch) -> FakeCI:
    fake = FakeCI([])
    monkeypatch.setattr(httpx, "get", fake.get)
    monkeypatch.setattr(httpx, "post", fake.post)
    monkeypatch.setattr(httpx, "delete", fake.delete)
    return fake


def test_a_known_agent_keeps_its_token(ci: FakeCI) -> None:
    ci.agents = [{"name": f"other-{n}", "token": f"t{n}"} for n in range(60)] + [
        {"name": "laptop", "token": "kept"}
    ]

    token, created = Woodpecker(URL, "http://f").ensure_agent("admin", "laptop")

    assert (token, created) == ("kept", False)
    assert ci.posted == []


def test_a_missing_agent_is_made_as_a_schedulable_global_agent(ci: FakeCI) -> None:
    token, created = Woodpecker(URL, "http://f").ensure_agent("admin", "laptop")

    assert (token, created) == ("new", True)
    assert ci.posted == [{"name": "laptop", "no_schedule": False}]
    assert set(ci.headers) == {"Bearer admin"}


def test_a_refused_enrolment_stops_the_run(ci: FakeCI) -> None:
    ci.create_status = 403

    with pytest.raises(WoodpeckerError, match="403"):
        Woodpecker(URL, "http://f").ensure_agent("admin", "laptop")


def test_of_two_agents_by_the_name_the_one_env_holds_is_kept(ci: FakeCI) -> None:
    ci.agents = [
        {"name": "laptop", "token": "stale"},
        {"name": "laptop", "token": "in-env"},
    ]

    token, created = Woodpecker(URL, "http://f").ensure_agent(
        "admin", "laptop", "in-env"
    )

    assert (token, created) == ("in-env", False)


NOW = 1_800_000_000.0
HOUR = 3600


def _agent(
    number: int, name: str, heard: float, pool: str | None = "platform"
) -> dict[str, Any]:
    return {
        "id": number,
        "name": name,
        "token": f"t{number}",
        "created": 0,
        "last_contact": int(heard),
        "custom_labels": {"pool": pool} if pool else {},
    }


def test_silent_agents_that_take_grading_runs_are_removed(ci: FakeCI) -> None:
    ci.agents = [
        _agent(1, "laptop", NOW - 5),
        _agent(3, "0b5d02212e01", NOW - 7 * 24 * HOUR),
        _agent(12, "platform-door", NOW - 2 * HOUR),
        _agent(14, "laptop", 0),
        _agent(20, "org-machine", NOW - 30 * 24 * HOUR, pool="acme"),
    ]

    found = Woodpecker(URL, "http://f").remove_other_agents(
        "admin", "t1", "laptop", ("pool", "platform"), now=NOW
    )

    assert ci.deleted == ["3", "12", "14"]
    assert found.removed == ("0b5d02212e01", "platform-door", "laptop")
    assert found.reporting == ()
    assert set(ci.headers) == {"Bearer admin"}


def test_an_agent_still_reporting_is_left_and_named(ci: FakeCI) -> None:
    ci.agents = [
        _agent(1, "laptop", NOW - 5),
        _agent(2, "other-machine", NOW - 60),
        {**_agent(4, "just-made", 0), "created": int(NOW - 60)},
    ]

    found = Woodpecker(URL, "http://f").remove_other_agents(
        "admin", "t1", "laptop", ("pool", "platform"), now=NOW
    )

    assert ci.deleted == []
    assert found.reporting == ("other-machine", "just-made")


def test_an_agent_the_ci_will_not_delete_is_named(ci: FakeCI) -> None:
    ci.agents = [_agent(3, "old", NOW - 2 * HOUR)]
    ci.delete_status = 409

    found = Woodpecker(URL, "http://f").remove_other_agents(
        "admin", "t1", "laptop", ("pool", "platform"), now=NOW
    )

    assert found.refused == ("old",)
    assert found.removed == ()
