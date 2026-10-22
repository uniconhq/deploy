"""`machine/grading-machine.py` writes what the settings say: the wall on the
throwaway user's uid alone, every step's hosts file, the names pinned in
the machine's own, the rootless daemon's daemon.json, the cache's cap, the
cgroup option that keeps a box's memory kill off its step, and the agent's
environment and units; it refuses settings that would leave a machine
unfit to grade, and reads a self-test target as held, got through or not
tested; and sets a machine up closed, the agent disabled before anything
else and enabled only once the self-test passes, with what it runs
stubbed. What it does to a real machine ran on a stand-in and on a real
Ubuntu, by hand.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from bootstrap import main

SCRIPT = Path(__file__).resolve().parents[1] / "machine" / "grading-machine.py"
AGENT = (
    "woodpeckerci/woodpecker-agent:v3.18.1@sha256:"
    "73ee7cc63161b40bfefa4a26eae45c518a09124ab34f7e3c5e781df85e1ba4ac"
)
CGROUP_LINE = (
    "35 24 0:30 / /sys/fs/cgroup rw,nosuid,nodev,noexec,relatime shared:9 "
    "- cgroup2 cgroup2 rw,nsdelegate,memory_recursiveprot"
)
SETTINGS: dict[str, Any] = {
    "server": "ci.example.org:9000",
    "agent_secret_file": "/etc/unicon/agent-secret",
    "agent_image": AGENT,
    "agent_binary_sha256": {"aarch64": "b" * 64, "x86_64": "c" * 64},
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


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ("2001:db8::10:443", "IPv6 address in brackets"),
        ("[203.0.113.10]:443", "IPv6 address in brackets"),
        ("[2001:db8::10]", "not an address and a port"),
        ("203.0.113.10:0", "from 1 to 65535"),
        ("203.0.113.10:65536", "from 1 to 65535"),
    ],
)
def test_an_ambiguous_address_or_a_port_out_of_range_is_refused(
    machine: ModuleType, text: str, refusal: str
) -> None:
    with pytest.raises(machine.MachineError, match=refusal):
        machine.Target.parse(text)


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


def sizes(machine: ModuleType) -> Any:
    return machine.read_sizes(machine.SLOT_TABLE.read_text())


def slots_of(machine: ModuleType, cores: int, memory_gb: int, **changed: Any) -> Any:
    """The slots of a machine with `cores` CPUs, 0 on, and `memory_gb`."""
    return machine.plan_slots(
        sizes(machine), settings(machine, **changed), range(cores), memory_gb * 1024
    )


def laid_out(slots: Any) -> list[tuple[str, str, bool, int]]:
    return [(slot.name, slot.cpus, slot.early, slot.port) for slot in slots]


def test_the_slot_table_is_deploys_one_table_of_sizes(machine: ModuleType) -> None:
    assert [(size.name, size.cores, size.memory_mb) for size in sizes(machine)] == [
        ("small", 1, 2048),
        ("big", 2, 8192),
    ]
    with pytest.raises(machine.MachineError, match="no whole cores and memory_mb"):
        machine.read_sizes('{"small": {"cores": 0, "memory_mb": 8192}}')
    with pytest.raises(machine.MachineError, match="names no slot size"):
        machine.read_sizes("{}")


def test_the_forge_is_given_the_sizes_the_machine_reads(machine: ModuleType) -> None:
    """Bootstrap writes the forge's copy from the same table, so the two
    cannot drift; the forge's own test holds its default to it too."""
    written = main.slot_sizes(SCRIPT.parent.parent / main.SLOT_TABLE)

    assert main.SLOT_TABLE.name == machine.SLOT_TABLE.name
    assert written == ",".join(
        f"{size.name}:{size.cores}:{size.memory_mb}" for size in sizes(machine)
    )
    assert written == "small:1:2048,big:2:8192"


def test_a_machine_gets_small_slots_and_a_big_one_only_beside_two_small(
    machine: ModuleType,
) -> None:
    """8 cores and 16 GB: one core and 1.5 GB kept, a big slot, since three
    small ones still fit beside it, then those three, the first kept for
    the early lane, a quarter of four."""
    assert laid_out(slots_of(machine, 8, 16)) == [
        ("small-1", "1", True, 3000),
        ("small-2", "2", False, 3001),
        ("small-3", "3", False, 3002),
        ("big-1", "4-5", False, 3003),
    ]
    # A big slot would leave one small one: small ones alone.
    assert [slot.name for slot in slots_of(machine, 4, 16)] == [
        "small-1",
        "small-2",
        "small-3",
    ]
    # The memory runs out first.
    assert [slot.name for slot in slots_of(machine, 16, 8)] == [
        "small-1",
        "small-2",
        "small-3",
    ]
    # The smallest machine: one small slot, which is in no lane of its own.
    assert laid_out(slots_of(machine, 2, 4)) == [("small-1", "1", False, 3000)]
    # The memory a 4 GB machine reports is less than 4 GB.
    assert (
        len(machine.plan_slots(sizes(machine), settings(machine), range(2), 3584)) == 1
    )
    with pytest.raises(machine.MachineError, match="has 2 cores and 3584 MB"):
        machine.plan_slots(sizes(machine), settings(machine), range(2), 3583)


def test_the_early_lane_keeps_a_share_of_the_smallest_slots_but_one(
    machine: ModuleType,
) -> None:
    def early(**changed: Any) -> list[str]:
        return [
            slot.name for slot in slots_of(machine, 64, 256, **changed) if slot.early
        ]

    # 61 small slots and a big one: a quarter of 62, rounded.
    assert early() == [f"small-{number}" for number in range(1, 17)]
    assert early(early_share=0) == []
    # Every slot but one, the smallest first, so late work has a slot.
    assert early(slots={"small": 2, "big": 2}, early_share=0.75) == [
        "small-1",
        "small-2",
        "big-1",
    ]
    assert early(slots={"big": 3}, early_share=0.4) == ["big-1"]
    # At least one of two slots or more, however small the share.
    assert early(slots={"small": 2}, early_share=0.1) == ["small-1"]
    assert early(slots={"small": 1}) == []


def test_the_slot_counts_are_the_settings_when_given_and_must_fit(
    machine: ModuleType,
) -> None:
    assert laid_out(slots_of(machine, 16, 64, slots={"big": 2})) == [
        ("big-1", "1-2", True, 3000),
        ("big-2", "3-4", False, 3001),
    ]
    with pytest.raises(machine.MachineError, match="need 16 cores and 65536 MB"):
        slots_of(machine, 16, 64, slots={"big": 8})
    with pytest.raises(machine.MachineError, match="slots names huge"):
        slots_of(machine, 16, 64, slots={"huge": 1})
    with pytest.raises(
        machine.MachineError, match="no slot fits; this machine gives slots 0 cores"
    ):
        slots_of(machine, 1, 8)


def test_cpus_read_and_written_as_the_kernel_lists_them(machine: ModuleType) -> None:
    assert machine.read_cpu_list("0-3,6,8-9\n") == [0, 1, 2, 3, 6, 8, 9]
    assert machine.cpu_list([9, 1, 2, 3, 6, 8]) == "1-3,6,8-9"
    assert machine.cpu_list([5]) == "5"


def test_each_slots_agent_takes_one_workflow_under_its_labels_and_limits(
    machine: ModuleType,
) -> None:
    small, *_, big = slots_of(machine, 16, 64)
    changed = {"agent_environment": {"WOODPECKER_LOG_LEVEL": "debug"}}
    text = machine.agent_environment(settings(machine, **changed), "the-token", big)
    values = dict(line.split("=", 1) for line in text.splitlines())

    assert values["WOODPECKER_AGENT_LABELS"] == (
        "pool=platform,shape=rootless,size=*,lane=*"
    )
    assert values["WOODPECKER_MAX_WORKFLOWS"] == "1"
    assert values["WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_SET"] == "14-15"
    assert values["WOODPECKER_BACKEND_DOCKER_LIMIT_MEM"] == str(8192 * 2**20)
    assert values["WOODPECKER_BACKEND_DOCKER_LIMIT_MEM_SWAP"] == str(8192 * 2**20)
    assert values["WOODPECKER_HOSTNAME"].endswith("-big-1")
    assert values["WOODPECKER_AGENT_CONFIG_FILE"] == (
        "/var/lib/unicon-agent/big-1/agent.conf"
    )
    assert values["WOODPECKER_HEALTHCHECK_ADDR"] == "127.0.0.1:3013"
    assert values["WOODPECKER_BACKEND_DOCKER_VOLUMES"] == (
        "/etc/unicon/hosts:/etc/hosts:ro,/etc/unicon/cache-cap:/etc/unicon/cache-cap:ro"
    )
    assert values["DOCKER_HOST"] == "unix:///run/unicon-docker/docker.sock"
    assert values["WOODPECKER_AGENT_SECRET"] == "the-token"
    assert values["WOODPECKER_LOG_LEVEL"] == "debug"
    assert small.labels(settings(machine)).endswith(",size=small,lane=early")
    with pytest.raises(machine.MachineError, match="line break"):
        machine.agent_environment(settings(machine), "a\nb", small)


def test_every_slots_agent_runs_as_its_own_user_beside_the_daemons(
    machine: ModuleType,
) -> None:
    unit = machine.agent_unit(settings(machine), 1001)

    assert "User=unicon-agent\n" in unit
    assert "SupplementaryGroups=unicon-grader\n" in unit
    assert "Requires=unicon-wall.service unicon-cgroup.service\n" in unit
    # It starts only while the wall's table is loaded, checked as root.
    assert "ExecStartPre=+/usr/sbin/nft list table inet unicon-wall\n" in unit
    # Each instance, by its slot's name, has its own environment and state.
    assert "EnvironmentFile=/etc/unicon/agent-%i.env\n" in unit
    assert "ReadWritePaths=/var/lib/unicon-agent/%i\n" in unit
    assert "NoNewPrivileges=yes\n" in unit and "ProtectSystem=strict\n" in unit


def test_the_self_test_reads_each_slots_limits_from_inside_a_container(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container made with a slot's limits shows its cores, its memory and
    no swap; one the daemon did not hold to them fails, and so does a slice
    not capped."""
    slots = slots_of(machine, 16, 64)[-2:]
    made: list[tuple[str, ...]] = []
    seen = {"13": "13 2147483648 0", "14-15": "0-15 max max"}

    def docker(*arguments: str, **_: object) -> str:
        made.append(arguments)
        return seen[arguments[arguments.index("--cpuset-cpus") + 1]] + "\n"

    monkeypatch.setattr(machine, "docker", docker)
    monkeypatch.setattr(machine, "memory_total", lambda: 2**36)
    monkeypatch.setattr(machine, "CGROUP", tmp_path)
    held = tmp_path / "user.slice" / "user-1001.slice" / "memory.max"
    held.parent.mkdir(parents=True)
    held.write_text(f"{machine.slice_memory(65536)}\n")
    report = machine.Report()

    machine.check_slots(settings(machine), SimpleNamespace(pw_uid=1001), slots, report)

    assert report.failed == [
        "slot big-1's containers have cores 14-15, 8192 MB and no swap"
    ]
    assert made[1][:9] == (
        "run",
        "--rm",
        "--cpuset-cpus",
        "14-15",
        "--memory",
        str(8192 * 2**20),
        "--memory-swap",
        str(8192 * 2**20),
        "--entrypoint",
    )
    held.write_text("max\n")
    machine.check_slots(settings(machine), SimpleNamespace(pw_uid=1001), [], report)
    assert report.failed[-1].startswith("the throwaway user's slice is capped at")


def test_the_slice_of_every_container_leaves_the_system_its_memory(
    machine: ModuleType,
) -> None:
    assert machine.slice_drop_in(65536) == (
        f"[Slice]\nMemoryMax={(65536 - 1024) * 2**20}\nMemorySwapMax=0\n"
    )


def test_the_wall_loads_after_what_flushes_the_rules_and_stops_with_its_unit(
    machine: ModuleType,
) -> None:
    unit = machine.wall_unit(1001)

    assert "Before=network-pre.target user@1001.service\n" in unit
    assert "After=nftables.service\n" in unit
    assert "ExecStop=-/usr/sbin/nft delete table inet unicon-wall\n" in unit
    # A restart of nftables.service, whose rules file flushes every table,
    # restarts the wall after it; a reload loads it again.
    assert "PartOf=nftables.service\n" in unit
    assert "ReloadPropagatedFrom=nftables.service\n" in unit
    assert "ExecReload=/usr/sbin/nft -f /etc/unicon/wall.nft\n" in unit


def test_a_remount_for_local_memory_events_keeps_the_hierarchys_options(
    machine: ModuleType,
) -> None:
    mountinfo = (
        "24 30 0:22 / /sys rw,nosuid shared:7 - sysfs sysfs rw\n"
        "35 24 0:30 / /sys/fs/cgroup rw,nosuid,nodev,noexec,relatime shared:9 "
        "- cgroup2 cgroup2 rw,nsdelegate,memory_recursiveprot\n"
    )

    options = machine.cgroup_options(mountinfo)

    assert options == (
        "nosuid,nodev,noexec,relatime,nsdelegate,memory_recursiveprot,"
        "memory_localevents"
    )
    assert machine.cgroup_options(
        mountinfo.replace("prot\n", "prot,memory_localevents\n")
    ) == (options)
    unit = machine.cgroup_unit(1001, options)
    assert f"ExecStart=/usr/bin/mount -o remount,{options} /sys/fs/cgroup\n" in unit
    assert "Before=user@1001.service\n" in unit
    assert machine.remounted_with(unit) == options.split(",")
    with pytest.raises(machine.MachineError, match="not a cgroup2 hierarchy"):
        machine.cgroup_options(mountinfo.splitlines()[0])


@pytest.mark.parametrize(
    ("mountinfo", "taken"),
    [
        (CGROUP_LINE + ",memory_localevents\n", True),
        (CGROUP_LINE.replace("nsdelegate", "memory_localevents,nsdelegate"), True),
        (CGROUP_LINE + "\n", False),
        # The option on another cgroup2 mount, or in a path, is not taken.
        (
            CGROUP_LINE + "\n40 24 0:40 / /run/memory_localevents rw - cgroup2 cgroup2 "
            "rw,memory_localevents\n",
            False,
        ),
    ],
)
def test_the_remount_is_checked_on_the_hierarchys_own_line(
    machine: ModuleType, tmp_path: Path, mountinfo: str, taken: bool
) -> None:
    unit = machine.cgroup_unit(1001, "nsdelegate,memory_localevents")
    post = next(line for line in unit.splitlines() if line.startswith("ExecStartPost="))
    pattern = post.split("'")[1].replace("$$", "$")
    (tmp_path / "mountinfo").write_text(mountinfo)

    found = subprocess.run(["grep", "-Eq", pattern, str(tmp_path / "mountinfo")])

    assert post.startswith("ExecStartPost=/usr/bin/grep -Eq '")
    assert (found.returncode == 0) is taken


def test_the_self_test_names_each_option_the_remount_lost(
    machine: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unit = tmp_path / "unicon-cgroup.service"
    unit.write_text(machine.cgroup_unit(1001, "nosuid,nodev,nsdelegate"))
    monkeypatch.setattr(machine, "CGROUP_UNIT", unit)
    monkeypatch.setattr(machine, "docker", lambda *_, **__: "false")
    monkeypatch.setattr(
        machine,
        "docker_done",
        lambda *_, **__: subprocess.CompletedProcess([], 0, "1\n", ""),
    )
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "35 24 0:30 / /sys/fs/cgroup rw,nosuid shared:9 - cgroup2 cgroup2 rw\n"
    )
    monkeypatch.setattr(machine, "MOUNTINFO", mountinfo)
    report = machine.Report()

    machine.check_kills(settings(machine), report)

    printed = capsys.readouterr().out
    assert "FAIL /sys/fs/cgroup kept every option it was remounted with" in printed
    assert "(lost nodev,nsdelegate)" in printed


def test_the_cache_cap_is_the_setting_or_a_quarter_of_the_data_root(
    machine: ModuleType,
) -> None:
    assert machine.cache_cap(settings(machine), 24 * 2**30) == 6 * 2**30
    assert machine.cache_cap(settings(machine, cache_cap=2**31), 24 * 2**30) == 2**31


def test_a_system_other_than_ubuntu_24_04_is_warned_of(machine: ModuleType) -> None:
    assert machine.system_warning('ID=ubuntu\nVERSION_ID="24.04"\n') is None
    assert "this is debian 13, not Ubuntu 24.04" in machine.system_warning(
        'ID=debian\nVERSION_ID="13"\n'
    )


def test_a_file_is_written_new_with_its_mode_from_the_start(
    machine: ModuleType, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("kept")
    path = tmp_path / "agent.env"
    # A link left where the new file is made is replaced, not followed.
    (tmp_path / "agent.env.new").symlink_to(elsewhere)

    machine.write(path, "TOKEN=x\n", mode=0o600)

    assert path.read_text() == "TOKEN=x\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert elsewhere.read_text() == "kept"
    assert not (tmp_path / "agent.env.new").exists()


@pytest.mark.parametrize(
    ("control", "seen", "said", "held"),
    [
        ("connected", "ECONNREFUSED", "ok  ", True),
        ("connected", "TimeoutError", "ok  ", True),
        ("connected", "connected", "FAIL", False),
        ("ETIMEDOUT", "ECONNREFUSED", "--  ", True),
        ("connected", "ENETUNREACH", "--  ", True),
        ("answered", "gaierror", "ok  ", True),
    ],
)
def test_a_target_is_held_got_through_or_not_tested(
    machine: ModuleType,
    capsys: pytest.CaptureFixture[str],
    control: str,
    seen: str,
    said: str,
    held: bool,
) -> None:
    report = machine.Report()

    assert machine.held_at(report, "the outside", control, seen, "a step") is held

    assert capsys.readouterr().out.startswith(said)
    assert report.failed == ([] if held else ["the outside"])


@pytest.mark.parametrize(
    ("changed", "refusal"),
    [
        ({"shape": "root"}, "is not built"),
        ({"wall_allow": []}, "wall_allow is empty"),
        ({"agent_user": "unicon-grader"}, "grader_user and agent_user must differ"),
        ({"grader_user": "root"}, "is root, a user setup did not make"),
        ({"agent_user": "root"}, "is root, a user setup did not make"),
        ({"grader_user": "Not A User"}, "not a user name"),
        ({"test_image": "ubuntu:24.04"}, "test_image is pinned by its digest"),
        ({"agent_binary_sha256": {}}, "for x86_64 or aarch64"),
        ({"agent_binary_sha256": {"arm64": "b" * 64}}, "for x86_64 or aarch64"),
        ({"agent_binary_sha256": ["b" * 64]}, "an object of texts"),
        ({"step_hosts": ["forge.example.org"]}, "step_hosts is an object of texts"),
        ({"agent_environment": {"X": 1}}, "agent_environment is an object of texts"),
        ({"agent_environment": {"A B": "1"}}, "agent_environment names 'A B'"),
        ({"pool": "platform,shape=root"}, "not a label's value"),
        ({"pool": "a=b"}, "not a label's value"),
        ({"pool": ""}, "not a label's value"),
        ({"server": 9000}, "server is a text"),
        ({"data_root": 1}, "data_root is a path"),
        ({"pull": [1]}, "pull is a list of texts"),
        ({"wall_allow": "203.0.113.10:443"}, "wall_allow is a list of texts"),
        ({"slots": {"small": -1}}, "slots gives each size a whole number"),
        ({"slots": {"small": 1.5}}, "slots gives each size a whole number"),
        ({"early_share": 1}, "early_share is a number from 0 to below 1"),
        ({"early_share": True}, "early_share is a number from 0 to below 1"),
        (
            {"agent_environment": {"WOODPECKER_BACKEND_DOCKER_LIMIT_MEM": "1"}},
            "which the agent's slot sets",
        ),
        ({"limit_mem": 1}, "unknown settings: limit_mem"),
        ({"agent_image": "woodpeckerci/woodpecker-agent:v3.18.1"}, "its digest"),
        ({"step_hosts": {"forge": "forge.example.org"}}, "not an address"),
        (
            {"step_hosts": {"forge\n203.0.113.9 evil": "203.0.113.10"}},
            "not a host name",
        ),
        ({"machine_hosts": {"-ghcr.io": "140.82.112.34"}}, "not a host name"),
        ({"agent_binary_sha256": {"x86_64": "abc"}}, "SHA-256 in hex"),
        ({"cache_cap": 1024}, "at least 1 GiB"),
        ({"outside": ["2606:4700:4700::1111:443"]}, "in brackets"),
        ({"colour": "blue"}, "unknown settings: colour"),
    ],
)
def test_settings_that_leave_a_machine_unfit_are_refused(
    machine: ModuleType, changed: dict[str, Any], refusal: str
) -> None:
    with pytest.raises(machine.MachineError, match=refusal):
        settings(machine, **changed)


def test_a_user_who_can_log_in_is_refused_as_either_user(
    machine: ModuleType,
) -> None:
    person = pwd.getpwuid(os.getuid()).pw_name

    for name in ("grader_user", "agent_user"):
        with pytest.raises(machine.MachineError, match="a user setup did not make"):
            settings(machine, **{name: person})


@pytest.mark.parametrize("name", ["grader_user", "agent_user"])
def test_a_system_user_is_refused_as_either_user(
    machine: ModuleType, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A user that cannot log in but that setup did not make, such as the
    resolver's, is not taken: its home is not the one setup gives."""
    resolver = SimpleNamespace(
        pw_uid=991, pw_shell="/usr/sbin/nologin", pw_dir="/run/systemd"
    )
    monkeypatch.setattr(machine.pwd, "getpwnam", lambda _: resolver)

    with pytest.raises(machine.MachineError, match="a user setup did not make"):
        settings(machine, **{name: "systemd-resolve"})


def test_the_users_setup_made_are_taken_again(
    machine: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    homes = {
        "unicon-grader": "/home/unicon-grader",
        "unicon-agent": str(machine.AGENT_STATE),
    }
    monkeypatch.setattr(
        machine.pwd,
        "getpwnam",
        lambda user: SimpleNamespace(
            pw_uid=1001, pw_shell="/usr/sbin/nologin", pw_dir=homes[user]
        ),
    )

    settings(machine)


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


# Setup's order, with everything it runs stubbed.


@pytest.fixture
def stubbed(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, ...]]:
    """setup's way of changing the machine replaced by a record of each
    command and step, in order, and its files moved under `tmp_path`."""
    done: list[tuple[str, ...]] = []

    def run(*command: str, **_: object) -> subprocess.CompletedProcess[str]:
        done.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def docker(*arguments: str, **_: object) -> str:
        """The CLI's answers: two containers running, and, asked about a
        daemon that has stopped, the empty line it prints."""
        done.append(("docker", *arguments))
        return "c1 c2\n" if arguments[0] == "ps" else "\n"

    def as_user(user: str, uid: int, *command: str, **_: object) -> str:
        done.append((f"as {user}", *command))
        return ""

    def step(name: str, answer: object = None) -> Any:
        def stub(*_: object, **__: object) -> object:
            done.append((name,))
            return answer

        return stub

    monkeypatch.setattr(machine, "run", run)
    monkeypatch.setattr(machine, "docker", docker)
    monkeypatch.setattr(machine, "as_user", as_user)
    grader = SimpleNamespace(pw_uid=1001, pw_gid=1001, pw_dir=str(tmp_path))
    monkeypatch.setattr(machine, "make_users", step("make users", grader))
    for name in (
        "disable_root_docker",
        "allow_rootlesskit",
        "count_kills_locally",
        "start_daemon",
        "pull",
        "install_agent",
    ):
        monkeypatch.setattr(machine, name, step(name))
    monkeypatch.setattr(machine, "check_data_root", step("check_data_root", 2**35))
    monkeypatch.setattr(machine, "memory_total", lambda: 2**36)
    monkeypatch.setattr(machine, "machine_cpus", lambda: list(range(16)))
    monkeypatch.setattr(machine, "architecture", lambda: "aarch64")
    monkeypatch.setattr(machine.os, "geteuid", lambda: 0)
    monkeypatch.setattr(machine.os, "chown", lambda *_: None)
    monkeypatch.setattr(machine.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def getpwnam(name: str) -> SimpleNamespace:
        """Each user as setup makes it, by the home it gives."""
        grader = name == "unicon-grader"
        home = f"/home/{name}" if grader else str(machine.AGENT_STATE)
        return SimpleNamespace(
            pw_uid=999, pw_gid=999, pw_shell="/usr/sbin/nologin", pw_dir=home
        )

    monkeypatch.setattr(machine.pwd, "getpwnam", getpwnam)
    for name in (
        "UNITS",
        "STEP_HOSTS",
        "CACHE_CAP",
        "WALL_FILE",
        "UNICON",
        "OLD_AGENT_ENV",
        "AGENT_STATE",
        "SOCKET_TMPFILES",
    ):
        monkeypatch.setattr(machine, name, tmp_path / name.lower())
    (tmp_path / "hosts").write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(machine, "HOSTS", tmp_path / "hosts")
    return done


def setup_settings(machine: ModuleType, tmp_path: Path) -> Any:
    (tmp_path / "secret").write_text("agent-secret\n")
    return settings(machine, agent_secret_file=str(tmp_path / "secret"))


DISABLE_AGENT = ("systemctl", "disable", "--now", "unicon-agent.service")
KILL = ("docker", "kill", "c1", "c2")
STOP_DAEMON = ("as unicon-grader", "systemctl", "--user", "stop", "docker.service")
WALL_DOWN = ("systemctl", "stop", "unicon-wall.service")
WALL_UP = ("systemctl", "restart", "unicon-wall.service")
ENABLE_AGENT = (
    "systemctl",
    "enable",
    "--now",
    *(f"unicon-agent@small-{number}.service" for number in range(1, 14)),
    "unicon-agent@big-1.service",
)


def test_setup_enables_the_agent_last_once_the_self_test_passes(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def passes(_: object) -> int:
        stubbed.append(("self_test",))
        return 0

    monkeypatch.setattr(machine, "self_test", passes)

    assert machine.setup(setup_settings(machine, tmp_path)) == 0

    assert stubbed[0] == DISABLE_AGENT
    order = [stubbed.index(each) for each in (KILL, STOP_DAEMON, WALL_DOWN)]
    assert order == sorted(order)
    assert stubbed.index(WALL_DOWN) < stubbed.index(WALL_UP)
    assert stubbed[-2:] == [("self_test",), ENABLE_AGENT]


def test_setup_runs_one_agent_per_slot_and_clears_away_those_gone(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every agent, the one before slots and each slot's of the last setup,
    is disabled first; the environments of those gone, which hold the
    token, are removed; each slot gets its own, readable by root alone,
    and its state folder; and the slice is capped."""
    unicon, units = tmp_path / "unicon", tmp_path / "units"
    (units / "multi-user.target.wants").mkdir(parents=True)
    (units / "multi-user.target.wants" / "unicon-agent@old-1.service").touch()
    (units / "unicon-agent.service").touch()
    unicon.mkdir()
    (unicon / "agent-old-1.env").write_text("WOODPECKER_AGENT_SECRET=x\n")
    (tmp_path / "old_agent_env").write_text("WOODPECKER_AGENT_SECRET=x\n")
    monkeypatch.setattr(machine, "self_test", lambda _: 0)

    assert machine.setup(setup_settings(machine, tmp_path)) == 0

    assert stubbed[:3] == [
        DISABLE_AGENT,
        ("systemctl", "disable", "--now", "unicon-agent@old-1.service"),
        ("systemctl", "stop", "unicon-agent@*.service"),
    ]
    assert stubbed[-1] == ENABLE_AGENT
    names = [*(f"small-{number}" for number in range(1, 14)), "big-1"]
    assert sorted(path.name for path in unicon.glob("agent-*.env")) == sorted(
        f"agent-{name}.env" for name in names
    )
    for name in names:
        assert (unicon / f"agent-{name}.env").stat().st_mode & 0o777 == 0o600
        assert (tmp_path / "agent_state" / name).is_dir()
    assert not (tmp_path / "old_agent_env").exists()
    assert not (units / "unicon-agent.service").exists()
    assert (units / "unicon-agent@.service").is_file()
    assert (units / "user-1001.slice.d" / "unicon-memory.conf").read_text() == (
        machine.slice_drop_in(65536)
    )


def test_setup_changes_nothing_on_a_machine_no_slot_fits(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(machine, "memory_total", lambda: 2**31)

    with pytest.raises(machine.MachineError, match="no slot fits"):
        machine.setup(setup_settings(machine, tmp_path))

    assert stubbed == []


def test_setup_leaves_the_agent_disabled_when_the_self_test_fails(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(machine, "self_test", lambda _: 1)

    assert machine.setup(setup_settings(machine, tmp_path)) == 1

    assert stubbed[0] == DISABLE_AGENT
    assert ENABLE_AGENT not in stubbed


def test_setup_stopped_midway_leaves_the_agent_disabled(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object) -> None:
        raise machine.MachineError("no filesystem of its own")

    monkeypatch.setattr(machine, "check_data_root", fail)
    monkeypatch.setattr(machine, "self_test", lambda _: 0)

    with pytest.raises(machine.MachineError, match="no filesystem"):
        machine.setup(setup_settings(machine, tmp_path))

    assert stubbed[0] == DISABLE_AGENT
    assert ENABLE_AGENT not in stubbed


def test_setup_keeps_the_wall_up_when_the_daemon_will_not_stop(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(machine, "docker", lambda *a, **_: "29.0.1\n")

    with pytest.raises(machine.MachineError, match="the wall stays up"):
        machine.setup(setup_settings(machine, tmp_path))

    assert WALL_DOWN not in stubbed
    assert ENABLE_AGENT not in stubbed


def test_setup_changes_nothing_on_a_machine_the_agent_binary_is_not_pinned_for(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(machine, "architecture", lambda: "riscv64")

    with pytest.raises(machine.MachineError, match="no digest for this machine's"):
        machine.setup(setup_settings(machine, tmp_path))

    assert stubbed == []
