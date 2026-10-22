"""`machine/grading-machine.py` writes what the settings say: the wall on the
throwaway user's uid alone, every step's hosts file, the names pinned in
the machine's own, the rootless daemon's daemon.json, and the agent's
environment and unit; and it refuses settings that would leave a machine
unfit to grade. What it does to a machine ran on a stand-in, by hand: see
the stage 4 plan's section 9.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "machine" / "grading-machine.py"
AGENT = (
    "woodpeckerci/woodpecker-agent:v3.18.1@sha256:"
    "73ee7cc63161b40bfefa4a26eae45c518a09124ab34f7e3c5e781df85e1ba4ac"
)
SETTINGS: dict[str, Any] = {
    "server": "ci.example.org:9000",
    "agent_secret_file": "/etc/unicon/agent-secret",
    "agent_image": AGENT,
    "data_root": "/var/lib/unicon-docker",
    "step_hosts": {"forge.example.org": "203.0.113.10"},
    "machine_hosts": {"ghcr.io": "140.82.112.34", "ci.example.org": "203.0.113.12"},
    "wall_allow": ["203.0.113.10:443", "[2001:db8::10]:443", "140.82.112.34:443"],
    "test_image": "ghcr.io/uniconhq/primitive-base@sha256:" + "a" * 64,
}


@pytest.fixture(scope="module")
def machine() -> ModuleType:
    spec = importlib.util.spec_from_file_location("grading_machine", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["grading_machine"] = module
    spec.loader.exec_module(module)
    return module


def settings(machine: ModuleType, **changed: Any) -> Any:
    return machine.Settings.of({**SETTINGS, **changed})


def test_an_address_and_port_is_read_for_ipv4_and_ipv6(machine: ModuleType) -> None:
    four = machine.Target.parse("203.0.113.10:443")
    six = machine.Target.parse("[2001:db8::10]:443")

    assert (four.address, four.port, four.family) == ("203.0.113.10", 443, "ip")
    assert (six.address, six.port, six.family) == ("2001:db8::10", 443, "ip6")
    assert str(six) == "[2001:db8::10]:443"
    with pytest.raises(machine.MachineError, match="not an address and a port"):
        machine.Target.parse("forge.example.org:443")


def test_the_wall_holds_the_throwaway_users_traffic_alone(machine: ModuleType) -> None:
    table = machine.wall_table(
        1001, [machine.Target.parse(t) for t in SETTINGS["wall_allow"]]
    )

    assert table.splitlines()[:3] == [
        "table inet unicon-wall",
        "delete table inet unicon-wall",
        "table inet unicon-wall {",
    ]
    assert "    meta skuid != 1001 accept" in table
    assert "    ip daddr 203.0.113.10 tcp dport 443 accept" in table
    assert "    ip6 daddr 2001:db8::10 tcp dport 443 accept" in table
    # Everything else of the user's is refused, after the allowed ones.
    lines = table.splitlines()
    assert lines.index("    reject") > max(
        i for i, line in enumerate(lines) if "accept" in line
    )


def test_a_step_sees_the_forges_names_and_no_others(machine: ModuleType) -> None:
    assert machine.step_hosts({"proxy": "172.31.41.5"}) == (
        "127.0.0.1 localhost\n"
        "::1 localhost ip6-localhost ip6-loopback\n"
        "172.31.41.5 proxy\n"
    )


def test_the_pinned_names_replace_their_own_block_and_nothing_else(
    machine: ModuleType,
) -> None:
    first = machine.pinned_hosts("127.0.0.1 localhost\n", {"ghcr.io": "140.82.112.34"})
    again = machine.pinned_hosts(first, {"ghcr.io": "140.82.112.35"})

    assert again == (
        "127.0.0.1 localhost\n"
        "\n"
        "# unicon machine names: begin\n"
        "140.82.112.35 ghcr.io\n"
        "# unicon machine names: end\n"
    )
    assert machine.pinned_hosts(again, {"ghcr.io": "140.82.112.35"}) == again


def test_the_daemon_tells_containers_to_ask_nowhere(machine: ModuleType) -> None:
    document = json.loads(machine.daemon_json(settings(machine), 1001))

    assert document == {
        "dns": ["192.0.2.1"],
        "data-root": "/var/lib/unicon-docker",
        "group": "root",
        "hosts": [
            "unix:///run/user/1001/docker.sock",
            "unix:///run/unicon-docker/docker.sock",
        ],
    }
    with_registry = settings(machine, insecure_registries=["localhost:5000"])
    assert json.loads(machine.daemon_json(with_registry, 1001))[
        "insecure-registries"
    ] == ["localhost:5000"]


def test_the_agent_gets_its_labels_hosts_and_memory(machine: ModuleType) -> None:
    text = machine.agent_environment(
        settings(machine, agent_environment={"WOODPECKER_LOG_LEVEL": "debug"}),
        "the-token",
        17179869184,
    )
    values = dict(line.split("=", 1) for line in text.splitlines())

    assert values["WOODPECKER_AGENT_LABELS"] == "pool=platform,shape=rootless"
    assert values["WOODPECKER_BACKEND_DOCKER_VOLUMES"] == (
        "/etc/unicon/hosts:/etc/hosts:ro"
    )
    assert values["WOODPECKER_BACKEND_DOCKER_LIMIT_MEM"] == "17179869184"
    assert values["DOCKER_HOST"] == "unix:///run/unicon-docker/docker.sock"
    assert values["WOODPECKER_AGENT_SECRET"] == "the-token"
    assert values["WOODPECKER_LOG_LEVEL"] == "debug"
    with pytest.raises(machine.MachineError, match="line break"):
        machine.agent_environment(settings(machine), "a\nb", 1)


def test_the_agent_runs_as_its_own_user_beside_the_daemons(
    machine: ModuleType,
) -> None:
    unit = machine.agent_unit(settings(machine), 1001)

    assert "User=unicon-agent\n" in unit
    assert "SupplementaryGroups=unicon-grader\n" in unit
    assert "Requires=unicon-wall.service\n" in unit
    assert "Before=network-pre.target user@1001.service\n" in machine.wall_unit(1001)


@pytest.mark.parametrize(
    ("changed", "refusal"),
    [
        ({"shape": "root"}, "is not built"),
        ({"wall_allow": []}, "wall_allow is empty"),
        ({"agent_user": "unicon-grader"}, "a user of its own"),
        ({"agent_image": "woodpeckerci/woodpecker-agent:v3.18.1"}, "its digest"),
        ({"step_hosts": {"forge": "forge.example.org"}}, "not an address"),
        ({"colour": "blue"}, "unknown settings: colour"),
    ],
)
def test_settings_that_leave_a_machine_unfit_are_refused(
    machine: ModuleType, changed: dict[str, Any], refusal: str
) -> None:
    with pytest.raises(machine.MachineError, match=refusal):
        settings(machine, **changed)


def test_a_missing_setting_is_named(machine: ModuleType) -> None:
    raw = {k: v for k, v in SETTINGS.items() if k not in ("server", "test_image")}

    with pytest.raises(machine.MachineError, match="missing settings: server, test"):
        machine.Settings.of(raw)


def test_the_subordinate_ids_are_counted_over_every_range(
    machine: ModuleType, tmp_path: Path
) -> None:
    table = tmp_path / "subuid"
    table.write_text("other:100000:65536\nunicon-grader:165536:30000\n")
    assert machine.subordinate_count(table, "unicon-grader") == 30000
    table.write_text(table.read_text() + "unicon-grader:300000:40000\n")
    assert machine.subordinate_count(table, "unicon-grader") == 70000
