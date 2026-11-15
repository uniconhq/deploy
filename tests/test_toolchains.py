"""The local stack persists resolver indexes and seeds named toolchains."""

from pathlib import Path

from bootstrap.core_yaml import load
from devseed import LANGUAGES, contests, submissions, task_request


def test_the_backend_has_a_persistent_resolver_folder() -> None:
    compose = load(Path("compose.yaml").read_bytes())
    services = compose["services"]
    expected = "/var/cache/unicon/resolve"
    assert "resolve-cache" in compose["volumes"]
    service = services["backend"]
    assert service["environment"]["UNICON_RESOLVE_CACHE"] == expected
    assert f"resolve-cache:{expected}" in service["volumes"]


def test_the_seed_names_cpp_sixteen_and_java_twenty_five() -> None:
    spring = load(task_request(contests.SPRING, "sum-of-two"))
    assert spring["environment"] == {"cpp": {"version": "16"}}
    assert spring["inputs"]["language"]["entrant"]["options"] == ["python", "cpp"]
    relay = load(task_request(contests.RELAY, "java-sum"))
    assert "java-sum" in contests.RELAY_TASKS
    assert relay["environment"] == {"java": {"version": "25"}}
    assert relay["inputs"]["language"]["entrant"]["options"] == ["java"]
    assert (
        Path("devseed/programs/summer-relay-2026/java-sum") / "bob-1.java"
    ).is_file()


def test_the_seeds_workflows_offer_every_language_classic_does() -> None:
    classic = Path("devseed/workflows/judgekit/classic/v1/request.yaml")
    offered = load(classic.read_bytes())["inputs"]["language"]["options"]
    for name in (
        "judgekit/special",
        "riverside/standard",
        "riverside/coins",
        "dan/round-trip",
    ):
        definition = Path("devseed/workflows", name, "v1", "request.yaml")
        assert load(definition.read_bytes())["inputs"]["language"]["options"] == offered
    assert sorted(LANGUAGES.values()) == sorted(offered)


def test_the_seed_has_kotlin_with_junit_5_and_typescript_with_chai() -> None:
    kotlin = load(task_request(contests.RELAY, "kotlin-sum"))
    assert kotlin["environment"] == {
        "kotlin": {"packages": ["org.junit.jupiter:junit-jupiter:5.+"]}
    }
    typescript = load(task_request(contests.RELAY, "typescript-sum"))
    assert typescript["environment"] == {
        "typescript": {"packages": ["chai@6", "@types/chai@5", "@types/node@26"]}
    }
    assert {"kotlin-sum", "typescript-sum"} <= set(contests.RELAY_TASKS)


def test_each_seeded_program_is_in_a_language_its_task_offers() -> None:
    for _, contest, task, program in submissions():
        offered = load(task_request(contest, task))["inputs"]["language"]
        assert LANGUAGES[program.suffix] in offered["entrant"]["options"], program
