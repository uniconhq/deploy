"""The local stack persists resolver indexes and seeds named toolchains."""

from pathlib import Path

from bootstrap.core_yaml import load
from devseed import contests, task_files


def test_the_backend_has_a_persistent_resolver_folder() -> None:
    compose = load(Path("compose.yaml").read_bytes())
    services = compose["services"]
    expected = "/var/cache/unicon/resolve"
    assert "resolve-cache" in compose["volumes"]
    service = services["backend"]
    assert service["environment"]["UNICON_RESOLVE_CACHE"] == expected
    assert f"resolve-cache:{expected}" in service["volumes"]


def test_the_seed_names_cpp_fourteen_and_java_twenty_one() -> None:
    spring = load(task_files(contests.SPRING, "sum-of-two")["task.yaml"])
    assert spring["environment"] == {"cpp": {"version": "14"}}
    assert spring["inputs"]["language"]["entrant"]["options"] == ["python", "cpp"]
    relay = load(task_files(contests.RELAY, "java-sum")["task.yaml"])
    assert "java-sum" in contests.RELAY_TASKS
    assert relay["environment"] == {"java": {"version": "21"}}
    assert relay["inputs"]["language"]["entrant"]["options"] == ["java"]
    assert (
        Path("devseed/programs/summer-relay-2026/java-sum") / "bob-1.java"
    ).is_file()
