"""`machine/grading-machine.py` writes what the settings say: the wall on the
throwaway user's uid alone, the names pinned in the machine's own hosts
file, each slot's disk in the machine's fstab, each slot's rootless
daemon's daemon.json and their unit, the cache's cap, the cgroup option
that keeps a box's memory kill off its step, each slot's runner's config,
the base runner's, the runners' units, and the low-disk check's; it
refuses settings that would leave a machine unfit to grade, makes each
disk once and keeps it after, reads a self-test target as held, got
through or not tested, and a slot's disk and the shared cache as the
self-test sees them; it sets a machine up closed, the runners disabled
before anything else and enabled only once the self-test passes, registers
each runner once, and its low-disk check stops a nearly full slot's runner
only once it is idle, with what each runs stubbed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from bootstrap import images, main

SCRIPT = Path(__file__).resolve().parents[1] / "machine" / "grading-machine.py"
RUNNER = "data.forgejo.org/forgejo/runner:13.2.1@sha256:" + "d" * 64
HELPER = "ghcr.io/uniconhq/helper@sha256:" + "e" * 64
CGROUP_LINE = (
    "35 24 0:30 / /sys/fs/cgroup rw,nosuid,nodev,noexec,relatime shared:9 "
    "- cgroup2 cgroup2 rw,nsdelegate,memory_recursiveprot"
)
SETTINGS: dict[str, Any] = {
    "server": "https://forge.example.org",
    "runner_token_file": "/etc/unicon/runner-token",
    "runner_image": RUNNER,
    "runner_binary_sha256": {"aarch64": "b" * 64, "x86_64": "c" * 64},
    "machine_hosts": {"ghcr.io": "140.82.112.34", "forge.example.org": "203.0.113.12"},
    "wall_allow": ["203.0.113.10:443", "[2001:db8::10]:443", "140.82.112.34:443"],
    "test_image": "ghcr.io/uniconhq/primitive-exec@sha256:" + "a" * 64,
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


def test_each_slots_daemon_tells_containers_to_ask_nowhere_from_its_own_disk(
    machine: ModuleType,
) -> None:
    """Its data root on its slot's disk, and what two daemons of one user
    would otherwise share, the exec root, the pidfile and the default
    socket, in a runtime folder of its own."""
    document = json.loads(machine.daemon_json(settings(machine), 1001, "small-2"))

    assert document == {
        "dns": ["192.0.2.1"],
        "data-root": "/var/lib/unicon/slots/small-2",
        "exec-root": "/run/user/1001/unicon-docker-small-2/exec",
        "pidfile": "/run/user/1001/unicon-docker-small-2/docker.pid",
        "group": "root",
        "hosts": [
            "unix:///run/user/1001/unicon-docker-small-2/docker.sock",
            "unix:///run/unicon-docker/small-2.sock",
        ],
    }
    with_registry = settings(machine, insecure_registries=["localhost:5000"])
    assert json.loads(machine.daemon_json(with_registry, 1001, "small-2"))[
        "insecure-registries"
    ] == ["localhost:5000"]


def test_every_daemon_starts_only_on_its_mounted_disk_with_its_own_state(
    machine: ModuleType,
) -> None:
    """The unit the rootless setup tool writes, made an instance by the
    slot's name: it does not start onto the bare mount point of a disk that
    did not mount, and rootlesskit's state and the config are the slot's
    own."""
    unit = machine.daemon_unit()

    assert "ConditionPathIsMountPoint=/var/lib/unicon/slots/%i\n" in unit
    assert "ConditionPathIsMountPoint=/var/lib/unicon/cache\n" in unit
    assert (
        "Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_STATE_DIR="
        "%t/unicon-docker-%i/rootlesskit\n"
    ) in unit
    assert "ExecStartPre=/usr/bin/mkdir -p -m 0700 %t/unicon-docker-%i\n" in unit
    assert (
        "dockerd-rootless.sh --config-file %h/.config/docker/daemon-%i.json\n"
    ) in unit
    assert "Delegate=yes\n" in unit and "Type=notify\n" in unit
    assert "WantedBy=default.target\n" in unit


def sizes(machine: ModuleType) -> Any:
    return machine.read_sizes(machine.SLOT_TABLE.read_text())


def slots_of(
    machine: ModuleType,
    cores: int,
    memory_gb: int,
    disk_gb: int = 2000,
    **changed: Any,
) -> Any:
    """The slots of a machine with `cores` CPUs, 0 on, `memory_gb`, and
    `disk_gb` for the disks setup makes."""
    return machine.plan_slots(
        sizes(machine),
        settings(machine, **changed),
        range(cores),
        memory_gb * 1024,
        disk_gb * 2**30,
    )


def laid_out(slots: Any) -> list[tuple[str, str, str]]:
    return [(slot.name, slot.cpus, slot.box_users) for slot in slots]


def test_the_slot_table_is_deploys_one_table_of_sizes(machine: ModuleType) -> None:
    assert [
        (size.name, size.cores, size.memory_mb, size.disk_gb) for size in sizes(machine)
    ] == [
        ("small", 1, 2048, 20),
        ("big", 2, 8192, 40),
    ]
    with pytest.raises(machine.MachineError, match="no whole cores, memory_mb and"):
        machine.read_sizes('{"small": {"cores": 0, "memory_mb": 8192, "disk_gb": 20}}')
    with pytest.raises(machine.MachineError, match="no whole cores, memory_mb and"):
        machine.read_sizes('{"small": {"cores": 1, "memory_mb": 8192}}')
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
    """8 cores and 16 GB: one core and 1 GB kept, and 256 MB beside each
    slot for its daemon, a big slot, since three small ones still fit
    beside it, then those three."""
    assert laid_out(slots_of(machine, 8, 16)) == [
        ("small-1", "1", "10000-10099"),
        ("small-2", "2", "10100-10199"),
        ("small-3", "3", "10200-10299"),
        ("big-1", "4-5", "10300-10399"),
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
    # The smallest machine: one small slot.
    assert laid_out(slots_of(machine, 2, 4)) == [("small-1", "1", "10000-10099")]
    # The memory a 4 GB machine reports is less than 4 GB.
    disk = 40 * 2**30
    assert (
        len(machine.plan_slots(sizes(machine), settings(machine), range(2), 3328, disk))
        == 1
    )
    with pytest.raises(machine.MachineError, match="has 2 cores, 3328 MB of memory"):
        machine.plan_slots(sizes(machine), settings(machine), range(2), 3327, disk)
    # Each slot holds its daemon's memory too: four small slots and their
    # daemons in 11264 MB, where five slots' memory alone would fit.
    assert len(slots_of(machine, 16, 12)) == 4


def test_the_slot_counts_are_the_settings_when_given_and_must_fit(
    machine: ModuleType,
) -> None:
    assert laid_out(slots_of(machine, 16, 64, slots={"big": 2})) == [
        ("big-1", "1-2", "10000-10099"),
        ("big-2", "3-4", "10100-10199"),
    ]
    with pytest.raises(
        machine.MachineError, match="need 16 cores, 67584 MB and 320 GiB of disk"
    ):
        slots_of(machine, 16, 64, slots={"big": 8})
    with pytest.raises(machine.MachineError, match="need 4 cores, 16896 MB and 80 GiB"):
        slots_of(machine, 16, 64, 90, slots={"big": 2})
    with pytest.raises(machine.MachineError, match="slots names huge"):
        slots_of(machine, 16, 64, slots={"huge": 1})
    with pytest.raises(
        machine.MachineError, match="no slot fits; this machine gives slots 0 cores"
    ):
        slots_of(machine, 1, 8)


def test_a_machine_runs_only_the_slots_whose_disks_fit_on_its_disk(
    machine: ModuleType,
) -> None:
    """The disk is a third limit beside the cores and the memory, the
    cache's disk counted first: 4 cores and 8 GB fit three small slots,
    and 50 GiB beside a 20 GiB cache one small disk."""
    assert [slot.name for slot in slots_of(machine, 4, 8, 50)] == ["small-1"]
    assert len(slots_of(machine, 4, 8, 80)) == 3
    # A big slot's 40 GiB beside two small ones' needs 100 GiB.
    assert [slot.name for slot in slots_of(machine, 8, 16, 99)] == [
        "small-1",
        "small-2",
        "small-3",
    ]
    assert [slot.name for slot in slots_of(machine, 8, 16, 100)] == [
        "small-1",
        "small-2",
        "big-1",
    ]
    with pytest.raises(
        machine.MachineError,
        match=re.escape(
            "no slot fits; this machine gives slots 3 cores, 7168 MB and 19.0 GiB "
            "of disk beside the cache's. The smallest machine a small slot fits on "
            "has 2 cores, 3328 MB of memory (MemTotal) and 50 GiB free on its disk"
        ),
    ):
        slots_of(machine, 4, 8, 39)
    with pytest.raises(machine.MachineError, match="and 60 GiB free on its disk"):
        slots_of(machine, 4, 8, 39, cache_gb=30)


def test_cpus_read_and_written_as_the_kernel_lists_them(machine: ModuleType) -> None:
    assert machine.read_cpu_list("0-3,6,8-9\n") == [0, 1, 2, 3, 6, 8, 9]
    assert machine.cpu_list([9, 1, 2, 3, 6, 8]) == "1-3,6,8-9"
    assert machine.cpu_list([5]) == "5"


def test_each_slots_runner_takes_one_job_under_its_label_with_its_box_users(
    machine: ModuleType,
) -> None:
    small, *_, big = slots_of(machine, 16, 64)
    changed = {"runner_envs": {"TZ": "UTC"}}
    config = json.loads(
        machine.runner_config(settings(machine, **changed), HELPER, big, 9 * 2**30)
    )

    assert config == {
        "log": {"level": "info"},
        "runner": {
            "file": "/var/lib/unicon-runner/big-1/.runner",
            "capacity": 1,
            "timeout": "3600s",
            "envs": {
                "TZ": "UTC",
                "UNICON_BOX_USERS": "11300-11399",
                "UNICON_CACHE_CAP_BYTES": str(9 * 2**30),
            },
            "labels": [f"unicon-exec-big:docker://{HELPER}"],
        },
        "cache": {"enabled": False},
        "container": {
            "network": "",
            "privileged": True,
            "options": "",
            "valid_volumes": ["unicon-cache"],
            "force_pull": False,
            "docker_host": "-",
        },
    }
    assert small.label(HELPER) == f"unicon-exec-small:docker://{HELPER}"


def test_the_base_runner_takes_jobs_unprivileged_with_no_box_users_or_cache(
    machine: ModuleType,
) -> None:
    changed = {"runner_envs": {"TZ": "UTC"}}
    config = json.loads(machine.base_config(settings(machine, **changed), HELPER))

    assert config == {
        "log": {"level": "info"},
        "runner": {
            "file": "/var/lib/unicon-runner/base/.runner",
            "capacity": 4,
            "timeout": "3600s",
            "envs": {"TZ": "UTC"},
            "labels": [f"unicon-base:docker://{HELPER}"],
        },
        "cache": {"enabled": False},
        "container": {
            "network": "",
            "privileged": False,
            "options": "",
            "valid_volumes": [],
            "force_pull": False,
            "docker_host": "-",
        },
    }


def test_the_labels_name_the_step_helper_bootstrap_reads(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One pin, under the key bootstrap's labels read; a tag is refused."""
    assert machine.IMAGE_MANIFEST.name == images.RELEASE_MANIFEST
    assert machine.STEP_HELPER == images.STEP_HELPER
    manifest = tmp_path / "images.json"
    monkeypatch.setattr(machine, "IMAGE_MANIFEST", manifest)
    manifest.write_text(json.dumps({"images": {"step-helper": HELPER}}))

    assert machine.machine_helper() == HELPER

    for pinned in ({}, {"step-helper": "ghcr.io/uniconhq/step-helper:v1"}):
        manifest.write_text(json.dumps({"images": pinned}))
        with pytest.raises(machine.MachineError, match="pins no step-helper"):
            machine.machine_helper()


def test_no_two_slots_share_a_box_user_and_all_lie_where_exec_takes_them(
    machine: ModuleType,
) -> None:
    slots = slots_of(machine, 64, 512)
    ranges = [
        range(int(first), int(last) + 1)
        for first, last in (slot.box_users.split("-") for slot in slots)
    ]

    assert len(slots) > 50
    assert all(len(users) == 100 for users in ranges)
    assert len({user for users in ranges for user in users}) == 100 * len(slots)
    assert min(ranges[0]) >= 10_000 and max(ranges[-1]) <= 65_000


def test_every_slots_runner_runs_as_its_own_user_against_its_daemon(
    machine: ModuleType,
) -> None:
    unit = machine.runner_unit(settings(machine), 1001)

    assert "User=unicon-runner\n" in unit
    assert "SupplementaryGroups=unicon-grader\n" in unit
    assert "Requires=unicon-wall.service unicon-cgroup.service\n" in unit
    # It starts only while the wall's table is loaded, checked as root.
    assert "ExecStartPre=+/usr/sbin/nft list table inet unicon-wall\n" in unit
    # Nor while its slot's disk or the cache's is not mounted, as its
    # daemon does not start.
    assert "ConditionPathIsMountPoint=/var/lib/unicon/slots/%i\n" in unit
    assert "ConditionPathIsMountPoint=/var/lib/unicon/cache\n" in unit
    # Each instance, by its slot's name, has its own daemon, config and state.
    assert "Environment=DOCKER_HOST=unix:///run/unicon-docker/%i.sock\n" in unit
    assert (
        "ExecStart=/usr/local/bin/forgejo-runner daemon "
        "--config /var/lib/unicon-runner/%i/config.yml\n"
    ) in unit
    assert "ReadWritePaths=/var/lib/unicon-runner/%i\n" in unit
    assert "NoNewPrivileges=yes\n" in unit and "ProtectSystem=strict\n" in unit
    # The base runner's own unit, on the first slot's daemon.
    base = machine.runner_unit(settings(machine), 1001, "base", "small-1")
    assert "ConditionPathIsMountPoint=/var/lib/unicon/slots/small-1\n" in base
    assert "Environment=DOCKER_HOST=unix:///run/unicon-docker/small-1.sock\n" in base
    assert "ReadWritePaths=/var/lib/unicon-runner/base\n" in base


def test_the_self_test_reads_the_slices_cap(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(machine, "memory_total", lambda: 2**36)
    monkeypatch.setattr(machine, "CGROUP", tmp_path)
    held = tmp_path / "user.slice" / "user-1001.slice" / "memory.max"
    held.parent.mkdir(parents=True)
    held.write_text(f"{machine.slice_memory(65536)}\n")
    report = machine.Report()

    machine.check_slice(SimpleNamespace(pw_uid=1001), report)
    assert report.failed == []
    held.write_text("max\n")
    machine.check_slice(SimpleNamespace(pw_uid=1001), report)
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

    machine.check_kills(settings(machine), "small-1", report)

    printed = capsys.readouterr().out
    assert "FAIL /sys/fs/cgroup kept every option it was remounted with" in printed
    assert "(lost nodev,nsdelegate)" in printed


def test_the_cache_cap_is_the_setting_or_most_of_the_caches_disk(
    machine: ModuleType,
) -> None:
    """The cache's disk holds nothing else, so the cap defaults to 90 % of
    it, and the setting may not be more than the disk's filesystem, a
    little less than `cache_gb`, which the settings' own check counts
    with."""
    assert machine.cache_cap(settings(machine), 20 * 2**30) == 18 * 2**30
    assert machine.cache_cap(settings(machine, cache_cap=2**31), 20 * 2**30) == 2**31
    whole = settings(machine, cache_cap=20 * 2**30)
    with pytest.raises(machine.MachineError, match="the cache's disk's filesystem"):
        machine.cache_cap(whole, 20 * 2**30 - 2**28)


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
    path = tmp_path / "config.yml"
    # A link left where the new file is made is replaced, not followed.
    (tmp_path / "config.yml.new").symlink_to(elsewhere)

    machine.write(path, "TOKEN=x\n", mode=0o600)

    assert path.read_text() == "TOKEN=x\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert elsewhere.read_text() == "kept"
    assert not (tmp_path / "config.yml.new").exists()


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
        ({"shape": "rootless"}, "unknown settings: shape"),
        ({"wall_allow": []}, "wall_allow is empty"),
        ({"runner_user": "unicon-grader"}, "grader_user and runner_user must differ"),
        ({"grader_user": "root"}, "is root, a user setup did not make"),
        ({"runner_user": "root"}, "is root, a user setup did not make"),
        ({"grader_user": "Not A User"}, "not a user name"),
        ({"test_image": "ubuntu:24.04"}, "test_image is pinned by its digest"),
        ({"helper_image": HELPER}, "unknown settings: helper_image"),
        ({"runner_binary_sha256": {}}, "for x86_64 or aarch64"),
        ({"runner_binary_sha256": {"arm64": "b" * 64}}, "for x86_64 or aarch64"),
        ({"runner_binary_sha256": ["b" * 64]}, "an object of texts"),
        ({"runner_envs": {"X": 1}}, "runner_envs is an object of texts"),
        ({"runner_envs": {"A B": "1"}}, "runner_envs names 'A B'"),
        ({"server": 9000}, "server is a text"),
        ({"runner_token_file": 1}, "runner_token_file is a path"),
        ({"data_root": "/var/lib/unicon-docker"}, "unknown settings: data_root"),
        ({"cache_gb": 0}, "cache_gb is a whole number of GiB"),
        ({"cache_gb": 2.5}, "cache_gb is a whole number of GiB"),
        ({"cache_cap": 21 * 2**30}, "more than the cache's disk of 20 GiB"),
        ({"pull": [1]}, "pull is a list of texts"),
        ({"wall_allow": "203.0.113.10:443"}, "wall_allow is a list of texts"),
        ({"slots": {"small": -1}}, "slots gives each size a whole number"),
        ({"slots": {"small": 1.5}}, "slots gives each size a whole number"),
        ({"runner_envs": {"UNICON_BOX_USERS": "1-2"}}, "which the slot sets"),
        ({"runner_envs": {"UNICON_CACHE_CAP_BYTES": "1"}}, "which the slot sets"),
        ({"step_hosts": {}}, "unknown settings: step_hosts"),
        ({"early_share": 0.25}, "unknown settings: early_share"),
        ({"runner_image": "data.forgejo.org/forgejo/runner:13.2.1"}, "its digest"),
        ({"machine_hosts": {"forge": "forge.example.org"}}, "not an address"),
        (
            {"machine_hosts": {"forge\n203.0.113.9 evil": "203.0.113.10"}},
            "not a host name",
        ),
        ({"machine_hosts": {"-ghcr.io": "140.82.112.34"}}, "not a host name"),
        ({"runner_binary_sha256": {"x86_64": "abc"}}, "SHA-256 in hex"),
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

    for name in ("grader_user", "runner_user"):
        with pytest.raises(machine.MachineError, match="a user setup did not make"):
            settings(machine, **{name: person})


@pytest.mark.parametrize("name", ["grader_user", "runner_user"])
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


@pytest.mark.parametrize(
    ("name", "user", "uid", "home"),
    [
        ("grader_user", "unicon-grader", 991, "/home/unicon-grader"),
        ("runner_user", "unicon-runner", 1001, None),
    ],
)
def test_a_user_of_the_other_kind_is_refused_whatever_its_home(
    machine: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    user: str,
    uid: int,
    home: str | None,
) -> None:
    """A system user whose home happens to be the throwaway user's, or a
    person's whose home happens to be the runner's, is not one setup made:
    setup makes the throwaway user a person's uid and the runner's a system
    one."""
    found = SimpleNamespace(
        pw_uid=uid,
        pw_shell="/usr/sbin/nologin",
        pw_dir=home or str(machine.RUNNER_STATE),
    )
    monkeypatch.setattr(machine.pwd, "getpwnam", lambda _: found)

    with pytest.raises(machine.MachineError, match="a user setup did not make"):
        settings(machine, **{name: user})


def test_the_users_setup_made_are_taken_again(
    machine: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = {
        "unicon-grader": (1001, "/home/unicon-grader"),
        "unicon-runner": (998, str(machine.RUNNER_STATE)),
    }
    monkeypatch.setattr(
        machine.pwd,
        "getpwnam",
        lambda user: SimpleNamespace(
            pw_uid=made[user][0], pw_shell="/usr/sbin/nologin", pw_dir=made[user][1]
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

    def docker(slot: str, *arguments: str, **_: object) -> str:
        """The CLI's answers: two containers running, and, asked about a
        daemon that has stopped, the empty line it prints."""
        done.append(("docker", slot, *arguments))
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
        "start_daemons",
        "make_cache_volume",
        "pull",
        "install_runner",
    ):
        monkeypatch.setattr(machine, name, step(name))
    monkeypatch.setattr(machine, "make_disks", step("make_disks", 20 * 2**30))
    monkeypatch.setattr(machine, "disks_room", lambda: 2000 * 2**30)
    monkeypatch.setattr(machine, "memory_total", lambda: 2**36)
    monkeypatch.setattr(machine, "machine_cpus", lambda: list(range(16)))
    monkeypatch.setattr(machine, "architecture", lambda: "aarch64")
    monkeypatch.setattr(machine.os, "geteuid", lambda: 0)
    monkeypatch.setattr(machine.os, "chown", lambda *_: None)
    monkeypatch.setattr(machine.shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def getpwnam(name: str) -> SimpleNamespace:
        """Each user as setup makes it, by the home and the kind of uid it
        gives: the throwaway user a person's, the runner's a system one."""
        grader = name == "unicon-grader"
        home = f"/home/{name}" if grader else str(machine.RUNNER_STATE)
        uid = 1001 if grader else 999
        return SimpleNamespace(
            pw_uid=uid, pw_gid=uid, pw_shell="/usr/sbin/nologin", pw_dir=home
        )

    monkeypatch.setattr(machine.pwd, "getpwnam", getpwnam)
    for name in (
        "UNITS",
        "WALL_FILE",
        "UNICON",
        "RUNNER_STATE",
        "SOCKET_TMPFILES",
        "SOCKET_DIRECTORY",
        "ROOM_SCRIPT",
        "ROOM_STATE",
    ):
        monkeypatch.setattr(machine, name, tmp_path / name.lower())
    (tmp_path / "hosts").write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(machine, "HOSTS", tmp_path / "hosts")
    (tmp_path / "images.json").write_text(
        json.dumps({"images": {"step-helper": HELPER}})
    )
    monkeypatch.setattr(machine, "IMAGE_MANIFEST", tmp_path / "images.json")
    # One daemon of an earlier setup, found by its socket.
    (tmp_path / "socket_directory").mkdir()
    (tmp_path / "socket_directory" / "small-1.sock").touch()
    return done


def setup_settings(machine: ModuleType, tmp_path: Path) -> Any:
    (tmp_path / "token").write_text("the-token\n")
    return settings(machine, runner_token_file=str(tmp_path / "token"))


DISABLE_ROOM = ("systemctl", "disable", "--now", "unicon-slot-room.timer")
STOP_RUNNERS = ("systemctl", "stop", "unicon-runner@*.service")
KILL = ("docker", "small-1", "kill", "c1", "c2")
STOP_DAEMON = (
    "as unicon-grader",
    "systemctl",
    "--user",
    "stop",
    "unicon-docker@*.service",
)
WALL_DOWN = ("systemctl", "stop", "unicon-wall.service")
WALL_UP = ("systemctl", "restart", "unicon-wall.service")
ENABLE_RUNNERS = (
    "systemctl",
    "enable",
    "--now",
    *(f"unicon-runner@small-{number}.service" for number in range(1, 14)),
    "unicon-runner@big-1.service",
    "unicon-runner@base.service",
)
ENABLE_ROOM = ("systemctl", "enable", "--now", "unicon-slot-room.timer")


def test_setup_enables_the_runners_last_once_the_self_test_passes(
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

    # The low-disk check first, since it starts a runner it stopped.
    assert stubbed[0] == DISABLE_ROOM
    order = [
        stubbed.index(each)
        for each in (STOP_RUNNERS, KILL, STOP_DAEMON, WALL_DOWN, ("make_disks",))
    ]
    assert order == sorted(order)
    # The disks are made before any daemon starts, and every daemon starts
    # after /etc is written, which rootlesskit copies as it starts.
    assert stubbed.index(("make_disks",)) < stubbed.index(("start_daemons",))
    assert stubbed.index(("start_daemons",)) < stubbed.index(WALL_UP)
    assert stubbed.index(WALL_DOWN) < stubbed.index(WALL_UP)
    assert stubbed[-3:] == [("self_test",), ENABLE_RUNNERS, ENABLE_ROOM]


def test_setup_runs_one_runner_per_slot_and_clears_away_those_gone(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every runner of the last setup is disabled first; the state of a
    slot gone, which holds its runner's token, is removed; each slot gets
    its own config and is registered once; and the slice is capped."""
    units, state = tmp_path / "units", tmp_path / "runner_state"
    (units / "multi-user.target.wants").mkdir(parents=True)
    (units / "multi-user.target.wants" / "unicon-runner@old-1.service").touch()
    (state / "old-1").mkdir(parents=True)
    (state / "old-1" / ".runner").write_text("{}\n")
    (state / "small-2").mkdir()
    (state / "small-2" / ".runner").write_text("{}\n")
    (tmp_path / ".config/docker").mkdir(parents=True)
    for name in ("old-1", "small-1"):
        (tmp_path / ".config/docker" / f"daemon-{name}.json").write_text("{}\n")
    monkeypatch.setattr(machine, "self_test", lambda _: 0)

    assert machine.setup(setup_settings(machine, tmp_path)) == 0

    assert stubbed[:4] == [
        DISABLE_ROOM,
        ("systemctl", "stop", "unicon-slot-room.service"),
        ("systemctl", "disable", "--now", "unicon-runner@old-1.service"),
        STOP_RUNNERS,
    ]
    assert stubbed[-2:] == [ENABLE_RUNNERS, ENABLE_ROOM]
    names = [*(f"small-{number}" for number in range(1, 14)), "big-1", "base"]
    assert sorted(path.name for path in state.iterdir()) == sorted(names)
    slots = {
        slot.name: slot
        for slot in machine.machine_slots(setup_settings(machine, tmp_path))
    }
    for name in names[:-1]:
        # The cache's cap from its disk.
        assert (state / name / "config.yml").read_text() == machine.runner_config(
            setup_settings(machine, tmp_path), HELPER, slots[name], 18 * 2**30
        )
    assert (state / "base" / "config.yml").read_text() == machine.base_config(
        setup_settings(machine, tmp_path), HELPER
    )
    # Each runner without its `.runner` registered, as its own user.
    registered = [
        command
        for command in stubbed
        if command[5:7] == ("register", "--no-interactive")
    ]
    assert [command[-1] for command in registered] == [
        f"{state}/{name}/config.yml" for name in names if name != "small-2"
    ]
    assert registered[0][:5] == (
        "runuser",
        "-u",
        "unicon-runner",
        "--",
        "/usr/local/bin/forgejo-runner",
    )
    assert registered[0][7:11] == (
        "--instance",
        "https://forge.example.org",
        "--token",
        "the-token",
    )
    assert registered[0][11:13] == ("--name", f"{machine.socket.gethostname()}-small-1")
    assert (units / "unicon-runner@.service").is_file()
    assert (units / "unicon-runner@base.service").read_text() == machine.runner_unit(
        setup_settings(machine, tmp_path), 1001, "base", "small-1"
    )
    assert (units / "user-1001.slice.d" / "unicon-memory.conf").read_text() == (
        machine.slice_drop_in(65536)
    )
    # Each slot's daemon.json, and the daemons' unit, as the throwaway user.
    written = [
        command[-1]
        for command in stubbed
        if command[:3] == ("as unicon-grader", "sh", "-c")
    ]
    assert written == [
        *(f"daemon-{name}.json" for name in slots),
        "unicon-docker@.service",
    ]
    # The daemon of the slot gone is disabled, and its daemon.json removed.
    gone = tmp_path / ".config/docker/daemon-old-1.json"
    assert (
        "as unicon-grader",
        "systemctl",
        "--user",
        "disable",
        "unicon-docker@old-1.service",
    ) in stubbed
    kept = gone.with_name("daemon-small-1.json")
    assert ("as unicon-grader", "rm", "-f", str(gone)) in stubbed
    assert ("as unicon-grader", "rm", "-f", str(kept)) not in stubbed
    # The low-disk check, from a copy of the script, over every slot.
    assert (tmp_path / "room_script").read_text() == SCRIPT.read_text()
    assert (tmp_path / "room_script").stat().st_mode & 0o777 == 0o755
    assert (units / "unicon-slot-room.service").read_text() == machine.room_unit(
        "unicon-grader", machine.machine_slots(setup_settings(machine, tmp_path))
    )
    assert (units / "unicon-slot-room.timer").is_file()


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


def test_setup_leaves_the_runners_disabled_when_the_self_test_fails(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(machine, "self_test", lambda _: 1)

    assert machine.setup(setup_settings(machine, tmp_path)) == 1

    assert stubbed[0] == DISABLE_ROOM
    assert ENABLE_RUNNERS not in stubbed
    assert ENABLE_ROOM not in stubbed


def test_setup_stopped_midway_leaves_the_runners_disabled(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_: object) -> None:
        raise machine.MachineError("a disk did not mount")

    monkeypatch.setattr(machine, "make_disks", fail)
    monkeypatch.setattr(machine, "self_test", lambda _: 0)

    with pytest.raises(machine.MachineError, match="did not mount"):
        machine.setup(setup_settings(machine, tmp_path))

    assert stubbed[0] == DISABLE_ROOM
    assert ENABLE_RUNNERS not in stubbed
    assert ENABLE_ROOM not in stubbed


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
    assert ENABLE_RUNNERS not in stubbed


def test_setup_changes_nothing_on_a_machine_the_runner_is_not_pinned_for(
    machine: ModuleType,
    stubbed: list[tuple[str, ...]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(machine, "architecture", lambda: "riscv64")

    with pytest.raises(machine.MachineError, match="no digest for this machine's"):
        machine.setup(setup_settings(machine, tmp_path))

    assert stubbed == []


# The disks, made with what setup runs stubbed.


def test_each_disk_mounts_before_the_daemons_and_fstrim_skips_it(
    machine: ModuleType,
) -> None:
    """In the machine's fstab, replacing our block and nothing else: each
    disk loop-mounted before the throwaway user's systemd, the boot going on
    without one that fails, and `fstrim` skipping it, which would make its
    file sparse again."""
    disks = [
        machine.Disk(Path("/var/lib/unicon/slots/small-1.img"), 20 * 2**30),
        machine.Disk(Path("/var/lib/unicon/cache.img"), 20 * 2**30),
    ]
    current = "UUID=abc / ext4 defaults 0 1\n"

    first = machine.fstab(current, disks, 1001)

    assert first == (
        "UUID=abc / ext4 defaults 0 1\n"
        "\n"
        "# unicon disks: begin\n"
        "/var/lib/unicon/slots/small-1.img /var/lib/unicon/slots/small-1 ext4 "
        "loop,nofail,X-fstrim.notrim,x-systemd.before=user@1001.service 0 2\n"
        "/var/lib/unicon/cache.img /var/lib/unicon/cache ext4 "
        "loop,nofail,X-fstrim.notrim,x-systemd.before=user@1001.service 0 2\n"
        "# unicon disks: end\n"
    )
    assert machine.fstab(first, disks, 1001) == first
    assert machine.fstab(first, disks[1:], 1001).count("loop,") == 1


@pytest.fixture
def disk_machine(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """The disks' folders and the fstab under `tmp_path`; `fallocate` sizing
    the file it is given, and `mount` and `umount` changing what is
    mounted."""
    done: list[tuple[str, ...]] = []
    mounted: set[Path] = set()

    def run(*command: str, **_: object) -> subprocess.CompletedProcess[str]:
        done.append(command)
        if command[0] == "fallocate":
            with open(command[-1], "r+b") as file:
                file.truncate(int(command[2]))
        elif command[0] == "mount":
            mounted.add(Path(command[1]))
        elif command[0] == "umount":
            mounted.discard(Path(command[1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(machine, "run", run)
    monkeypatch.setattr(machine.os.path, "ismount", lambda path: Path(path) in mounted)
    monkeypatch.setattr(machine.os, "chown", lambda *_: None)
    monkeypatch.setattr(machine, "DISKS", tmp_path / "unicon")
    monkeypatch.setattr(machine, "SLOT_DISKS", tmp_path / "unicon" / "slots")
    monkeypatch.setattr(machine, "CACHE_DISK", tmp_path / "unicon" / "cache")
    (tmp_path / "fstab").write_text("UUID=abc / ext4 defaults 0 1\n")
    monkeypatch.setattr(machine, "FSTAB", tmp_path / "fstab")
    slots = tmp_path / "unicon" / "slots"

    def disks(*names: str, cache: int = 2**20) -> list[Any]:
        made = [machine.Disk(slots / f"{name}.img", 2**20) for name in names]
        return [*made, machine.Disk(tmp_path / "unicon" / "cache.img", cache)]

    return SimpleNamespace(
        done=done,
        mounted=mounted,
        disks=disks,
        slots=slots,
        grader=SimpleNamespace(pw_uid=1001, pw_gid=1001),
    )


def test_a_disk_is_made_once_reserved_whole_and_formatted_without_discard(
    machine: ModuleType, disk_machine: SimpleNamespace, tmp_path: Path
) -> None:
    """Made under another name, so a file of a disk's name is always a
    formatted disk, readable by root alone, then mounted, its root the
    throwaway user's and closed to everyone else."""
    disks = disk_machine.disks("small-1", "small-2")

    size = machine.make_disks(disks, disk_machine.grader)

    for disk in disks:
        made = f"{disk.image}.new"
        assert ("fallocate", "--length", str(2**20), made) in disk_machine.done
        assert (
            "mkfs.ext4",
            "-q",
            "-F",
            "-m",
            "0",
            "-E",
            "nodiscard",
            made,
        ) in disk_machine.done
        assert disk.image.stat().st_size == 2**20
        assert disk.image.stat().st_mode & 0o777 == 0o600
        assert disk.mount in disk_machine.mounted
        assert disk.mount.stat().st_mode & 0o777 == 0o700
    assert not list(disk_machine.slots.glob("*.new"))
    assert (tmp_path / "fstab").read_text() == machine.fstab(
        "UUID=abc / ext4 defaults 0 1\n", disks, 1001
    )
    assert size == machine.shutil.disk_usage(tmp_path / "unicon" / "cache").total


def test_setup_run_again_keeps_each_disk_and_reserves_it_again(
    machine: ModuleType, disk_machine: SimpleNamespace
) -> None:
    """The same slots again: no disk is formatted, each is reserved again,
    which fills any hole fstrim or anything else made and keeps its data,
    and one not mounted is mounted."""
    disks = disk_machine.disks("small-1")
    machine.make_disks(disks, disk_machine.grader)
    with open(disks[0].image, "r+b") as file:
        file.write(b"kept")
    disk_machine.mounted.discard(disks[0].mount)
    disk_machine.done.clear()

    machine.make_disks(disks, disk_machine.grader)

    assert not [c for c in disk_machine.done if c[0] == "mkfs.ext4"]
    assert ("fallocate", "--length", str(2**20), str(disks[0].image)) in (
        disk_machine.done
    )
    assert disks[0].image.read_bytes()[:4] == b"kept"
    assert ("mount", str(disks[0].mount)) in disk_machine.done
    assert ("mount", str(disks[1].mount)) not in disk_machine.done


def test_a_disk_of_a_slot_gone_is_removed_and_one_of_another_size_made_again(
    machine: ModuleType, disk_machine: SimpleNamespace, tmp_path: Path
) -> None:
    first = disk_machine.disks("small-1", "small-2")
    machine.make_disks(first, disk_machine.grader)
    disk_machine.done.clear()

    machine.make_disks(disk_machine.disks("small-1", cache=2**21), disk_machine.grader)

    gone = disk_machine.slots / "small-2"
    assert ("umount", str(gone)) in disk_machine.done
    assert not gone.exists() and not gone.with_suffix(".img").exists()
    cache = tmp_path / "unicon" / "cache"
    assert ("umount", str(cache)) in disk_machine.done
    formatted = ("mkfs.ext4", "-q", "-F", "-m", "0", "-E", "nodiscard")
    assert (*formatted, f"{cache}.img.new") in disk_machine.done
    assert cache.with_suffix(".img").stat().st_size == 2**21
    assert cache in disk_machine.mounted
    assert "small-2" not in (tmp_path / "fstab").read_text()


def test_a_disk_left_half_made_is_given_back_first(
    machine: ModuleType, disk_machine: SimpleNamespace, tmp_path: Path
) -> None:
    """A run stopped between reserving a disk and renaming it leaves its
    file under the name it was made with, which no disk is."""
    disk_machine.slots.mkdir(parents=True)
    for left in (disk_machine.slots / "small-9.img.new", tmp_path / "unicon/c.img.new"):
        left.write_bytes(b"x" * 4096)

    machine.make_disks(disk_machine.disks("small-1"), disk_machine.grader)

    assert not list((tmp_path / "unicon").glob("**/*.new"))
    assert (disk_machine.slots / "small-1.img").is_file()


def test_a_disk_that_does_not_mount_stops_setup(
    machine: ModuleType, disk_machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(machine.os.path, "ismount", lambda _: False)

    with pytest.raises(machine.MachineError, match="did not mount at"):
        machine.make_disks(disk_machine.disks("small-1"), disk_machine.grader)


def test_the_room_for_the_disks_counts_those_made_before_as_given_back(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What is free, and what each disk made before holds: one kept, one of
    a slot gone or of another size, and one left half made, since setup
    gives each back before it makes any. So setup run again finds the
    room it found before, and the slot plan refuses what does not fit
    (`plan_slots`) before anything is changed."""
    disks = tmp_path / "unicon"
    monkeypatch.setattr(machine, "DISKS", disks)
    monkeypatch.setattr(machine, "SLOT_DISKS", disks / "slots")
    (disks / "slots").mkdir(parents=True)
    for made in ("cache.img", "slots/small-1.img", "slots/small-2.img.new"):
        (disks / made).write_bytes(b"x" * 2**16)
    (disks / "slots" / "small-1").mkdir()
    kept = machine.SYSTEM_DISK_GB * 2**30
    monkeypatch.setattr(
        machine.shutil, "disk_usage", lambda _: SimpleNamespace(free=kept + 2**30)
    )

    assert machine.disks_room() == 2**30 + 3 * machine.allocated(disks / "cache.img")


def test_the_disks_leave_the_system_its_share_of_the_machines_disk(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disks = tmp_path / "unicon"
    monkeypatch.setattr(machine, "DISKS", disks)
    monkeypatch.setattr(machine, "SLOT_DISKS", disks / "slots")
    monkeypatch.setattr(
        machine.shutil, "disk_usage", lambda _: SimpleNamespace(free=5 * 2**30)
    )

    assert machine.disks_room() == 0


def test_the_disks_are_every_slots_and_the_caches(machine: ModuleType) -> None:
    slots = slots_of(machine, 16, 64)
    disks = machine.machine_disks(settings(machine, cache_gb=60), slots)

    assert [(str(disk.image), disk.size) for disk in disks[::13]] == [
        ("/var/lib/unicon/slots/small-1.img", 20 * 2**30),
        ("/var/lib/unicon/slots/big-1.img", 40 * 2**30),
    ]
    assert (disks[-1].image, disks[-1].mount, disks[-1].size) == (
        Path("/var/lib/unicon/cache.img"),
        Path("/var/lib/unicon/cache"),
        60 * 2**30,
    )


# The self-test's checks of the disks and the cache.


def running(listed: str) -> Any:
    """systemctl, asked which runners are active, listing `listed`."""

    def run(*command: str, **_: object) -> subprocess.CompletedProcess[str]:
        assert command[:2] == ("systemctl", "list-units")
        assert command[-1] == "unicon-runner@*.service"
        return subprocess.CompletedProcess(command, 0, listed, "")

    return run


def test_the_self_test_fills_each_slots_disk_and_watches_the_others(
    machine: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot whose container wrote past its disk without ENOSPC fails, one
    whose fill reached another slot's disk fails, and a disk whose file is
    not fully allocated fails."""
    slots = slots_of(machine, 4, 8)[:2]
    used = {slot.disk.mount: 2**28 for slot in slots}
    answers = {
        "small-1": {"filled": 19 * 2**30, "write": "ENOSPC"},
        "small-2": {"filled": 19 * 2**30, "write": "written"},
    }

    def docker(slot: str, *arguments: str, **_: object) -> str:
        assert "posix_fallocate" in arguments[-1]
        if slot == "small-1":
            used[slots[1].disk.mount] += 2**22
        return json.dumps(answers[slot]) + "\n"

    monkeypatch.setattr(machine, "docker", docker)
    monkeypatch.setattr(machine, "run", running(""))
    monkeypatch.setattr(
        machine.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(used=used[Path(path)]),
    )
    monkeypatch.setattr(machine.Path, "is_file", lambda _: True)
    monkeypatch.setattr(
        machine,
        "allocated",
        lambda path: 0 if path.name == "cache.img" else 20 * 2**30 + 4096,
    )
    report = machine.Report()

    machine.check_disks(settings(machine), slots, report)

    assert report.failed == [
        "/var/lib/unicon/cache.img is fully allocated on the machine's disk",
        "the other slots' disks stay as they were while slot small-1's is full",
        "a container of slot small-2 writing past its disk gets ENOSPC",
    ]


def test_the_self_test_fills_no_disk_while_a_runner_takes_gradings(
    machine: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run by hand on a machine that grades: a full disk would fail the
    gradings its runner takes, so the fill is not tested, not failed."""

    def docker(*_: object, **__: object) -> str:
        raise AssertionError("a disk was filled")

    monkeypatch.setattr(machine, "docker", docker)
    monkeypatch.setattr(
        machine,
        "run",
        running("unicon-runner@small-1.service loaded active running Unicon grading\n"),
    )
    monkeypatch.setattr(machine.Path, "is_file", lambda _: True)
    monkeypatch.setattr(machine, "allocated", lambda _: 40 * 2**30)
    report = machine.Report()

    machine.check_disks(settings(machine), slots_of(machine, 4, 8)[:2], report)

    assert report.failed == []
    assert (
        "--   a container of each slot writing past its disk gets ENOSPC (not "
        "tested: a runner takes gradings, unicon-runner@small-1.service; stop the "
        "runners first)"
    ) in capsys.readouterr().out


def test_the_self_test_reads_from_another_slot_what_one_wrote_to_the_cache(
    machine: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The first slot writes through its cache volume; every other slot
    reads the file through its own, owned by root inside as it was
    written; and the file is removed after."""
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(machine, "CACHE_DISK", cache)
    monkeypatch.setattr(machine.os.path, "ismount", lambda _: True)
    written: list[str] = []

    def docker_done(slot: str, *arguments: str, **_: object) -> Any:
        assert slot == "small-1"
        assert arguments[3] == "unicon-cache:/cache"
        written.append(arguments[-1].split("'")[-2])
        (cache / ".self-test").write_text(written[-1])
        return subprocess.CompletedProcess([], 0, "", "")

    def docker(slot: str, *arguments: str, **_: object) -> str:
        return f"{written[0]} 0 0\n" if slot == "small-2" else "nothing 1000 1000\n"

    monkeypatch.setattr(machine, "docker_done", docker_done)
    monkeypatch.setattr(machine, "docker", docker)
    grader = SimpleNamespace(pw_uid=os.getuid())
    report = machine.Report()

    machine.check_cache(settings(machine), grader, slots_of(machine, 4, 16), report)

    assert report.failed == [
        "slot small-3 reads what slot small-1 wrote to /cache, as the same owner"
    ]
    assert not (cache / ".self-test").exists()
    machine.check_cache(settings(machine), grader, slots_of(machine, 2, 4), report)
    assert "--   a write to /cache from one slot is read from another" in (
        capsys.readouterr().out
    )


def test_each_slots_daemon_binds_the_cache_volume_to_the_caches_disk_once(
    machine: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[tuple[str, ...]] = []
    found = {"small-1": 1, "small-2": 0}

    def docker_done(slot: str, *arguments: str, **_: object) -> Any:
        assert arguments == ("volume", "inspect", "unicon-cache")
        return subprocess.CompletedProcess([], found[slot], "", "")

    def docker(slot: str, *arguments: str, **_: object) -> str:
        made.append((slot, *arguments))
        return ""

    monkeypatch.setattr(machine, "docker_done", docker_done)
    monkeypatch.setattr(machine, "docker", docker)

    machine.make_cache_volume("small-1")
    machine.make_cache_volume("small-2")

    assert made == [
        (
            "small-1",
            "volume",
            "create",
            "--driver",
            "local",
            "--opt",
            "type=none",
            "--opt",
            "o=bind",
            "--opt",
            "device=/var/lib/unicon/cache",
            "--label",
            "unicon-cache",
            "unicon-cache",
        )
    ]


# The low-disk check, with what it runs stubbed.


def test_a_slot_is_nearly_full_below_a_tenth_of_its_disk_or_2_gib(
    machine: ModuleType,
) -> None:
    assert machine.room_mark(40 * 2**30) == 4 * 2**30
    assert machine.room_mark(20 * 2**30) == 2 * 2**30
    assert machine.room_mark(3 * 2**30) == 2 * 2**30


def test_the_low_disk_check_runs_every_minute_over_every_slot(
    machine: ModuleType,
) -> None:
    unit = machine.room_unit("unicon-grader", slots_of(machine, 3, 8))

    assert (
        "ExecStart=/usr/bin/python3 -I /usr/local/lib/unicon/grading-machine.py "
        "room --grader unicon-grader small-1 small-2\n"
    ) in unit
    assert "Type=oneshot\n" in unit
    assert "OnCalendar=minutely\n" in machine.room_timer()


@pytest.fixture
def room(
    machine: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """A slot `small-1` of a 20 GiB disk, mounted, its runner enabled, at
    `NOW`; the free space each look at the disk sees, in turn, and the
    names of the containers its daemon runs and the networks it has,
    which root never asks it for."""
    state = SimpleNamespace(
        done=[],
        free=[],
        containers="",
        networks=[],
        answers=0,
        enabled="enabled",
        mounted=True,
    )

    def run(*command: str, **_: object) -> subprocess.CompletedProcess[str]:
        assert command[0] != "docker"
        state.done.append(command)
        return subprocess.CompletedProcess(command, 0, state.enabled + "\n", "")

    def docker_as(grader: Any, slot: str, *arguments: str, **_: object) -> Any:
        assert grader is GRADER
        state.done.append(("docker", slot, *arguments))
        answer = ""
        if arguments[:2] == ("container", "ls"):
            answer = state.containers
        elif arguments[:2] == ("network", "ls"):
            answer = "".join(f"{network['Id']}\n" for network in state.networks)
        elif arguments[:2] == ("network", "inspect"):
            answer = json.dumps(
                [network for network in state.networks if network["Id"] in arguments]
            )
        return subprocess.CompletedProcess([], state.answers, answer, "")

    def disk_usage(path: Path) -> SimpleNamespace:
        assert Path(path) == tmp_path / "slots" / "small-1"
        return SimpleNamespace(total=20 * 2**30, free=state.free.pop(0))

    monkeypatch.setattr(machine, "run", run)
    monkeypatch.setattr(machine, "docker_as", docker_as)
    monkeypatch.setattr(machine, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(machine.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(machine.os.path, "ismount", lambda _: state.mounted)
    monkeypatch.setattr(machine, "SLOT_DISKS", tmp_path / "slots")
    monkeypatch.setattr(machine, "ROOM_STATE", tmp_path / "room")
    state.stopped = tmp_path / "room" / "small-1.stopped"
    return state


GRADER = SimpleNamespace(pw_name="unicon-grader", pw_uid=1001)
NOW = datetime.fromisoformat("2026-11-05T03:00:00+08:00").timestamp()
LISTED = [
    (
        "docker",
        "small-1",
        "container",
        "ls",
        "--filter",
        "name=^FORGEJO-ACTIONS-TASK-",
        "--format",
        "{{.Names}}",
    ),
    (
        "docker",
        "small-1",
        "network",
        "ls",
        "--quiet",
        "--filter",
        "name=^FORGEJO-ACTIONS-TASK-",
    ),
]
JOB = "FORGEJO-ACTIONS-TASK-7-WORKFLOW-3f2a-JOB-grade"


def network(name: str, created: str) -> dict[str, Any]:
    """A network as `docker network inspect` gives it, in part."""
    return {
        "Name": name,
        "Id": hashlib.sha256(name.encode()).hexdigest(),
        "Created": created,
        "Driver": "bridge",
        "Containers": {},
    }


STOP = ("systemctl", "stop", "unicon-runner@small-1.service")
START = ("systemctl", "start", "unicon-runner@small-1.service")
PRUNED = [
    ("docker", "small-1", "container", "prune", "--force"),
    ("docker", "small-1", "network", "prune", "--force"),
    ("docker", "small-1", "image", "prune", "--force"),
    (
        "docker",
        "small-1",
        "volume",
        "prune",
        "--all",
        "--force",
        "--filter",
        "label!=unicon-cache",
    ),
]


def test_a_nearly_full_slot_waits_while_a_job_runs(
    machine: ModuleType, room: SimpleNamespace
) -> None:
    """Stopping the runner would kill the job and leave its containers,
    network and volumes behind. A job pulling an image runs no container,
    but its network lives until it ends."""
    room.free = [2**30]
    room.containers = f"{JOB}\n"

    machine.check_room(GRADER, "small-1")

    assert STOP not in room.done
    assert not [c for c in room.done if "prune" in c]
    assert [c for c in room.done if c[0] == "docker"] == LISTED
    young = network(f"{JOB}-network", "2026-11-04T18:20:00Z")
    room.free, room.containers, room.networks = [2**30], "", [young]
    machine.check_room(GRADER, "small-1")
    assert STOP not in room.done
    assert room.done[-1] == ("docker", "small-1", "network", "inspect", young["Id"])
    room.free, room.answers = [2**30], 1
    machine.check_room(GRADER, "small-1")
    assert STOP not in room.done
    assert not [c for c in room.done if "prune" in c]


def test_a_job_network_older_than_any_job_is_a_leftover(
    machine: ModuleType, room: SimpleNamespace
) -> None:
    """A runner stopped mid-job cannot remove its job's network, which then
    has no container: past the longest a job runs, it no longer keeps the
    slot busy, and is removed with the rest."""
    room.free = [2**30, 10 * 2**30]
    room.networks = [network(f"{JOB}-network", "2026-11-04T17:59:59Z")]

    machine.check_room(GRADER, "small-1")

    assert room.done.index(STOP) < room.done.index(PRUNED[1]) < room.done.index(START)


def test_a_job_is_told_by_its_names_and_its_networks_age(
    machine: ModuleType,
) -> None:
    """The names are matched again here, whatever the daemon's filter
    matched, as Docker writes them; and its times, to the nanosecond,
    read."""
    young = "2026-11-05T02:59:59.123456789+08:00"
    assert machine.job_running(f"{JOB}\n", [], NOW)
    assert not machine.job_running(f"my-{JOB}\nother\n", [], NOW)
    assert machine.job_running("", [network(f"{JOB}-network", young)], NOW)
    assert not machine.job_running("", [network(f"a-{JOB}-network", young)], NOW)
    assert not machine.job_running(
        "", [network(f"{JOB}-network", "2026-11-05T01:59:59.5+08:00")], NOW
    )
    assert machine.job_running(
        "", [network(f"{JOB}-network", "2026-11-04T18:00:01Z")], NOW
    )
    assert machine.created_at("2026-11-05T02:59:59.999999999+08:00") == pytest.approx(
        NOW - 1e-6, abs=1e-5
    )
    with pytest.raises(machine.MachineError, match="a network's time"):
        machine.created_at("yesterday")


def test_an_idle_nearly_full_slot_stops_until_its_disk_has_room(
    machine: ModuleType, room: SimpleNamespace
) -> None:
    """Its runner stopped and its daemon cleaned up but for the cache's
    volume; still below the mark, the runner stays stopped, and a later
    look that finds room starts it."""
    room.free = [2**30, 2**30 + 2**20]

    machine.check_room(GRADER, "small-1")

    assert [c for c in room.done if c[0] == "docker"][2:] == PRUNED
    assert STOP in room.done and START not in room.done
    assert room.stopped.exists()
    room.done.clear()
    room.free = [3 * 2**30]
    machine.check_room(GRADER, "small-1")
    assert room.done[-1] == START
    assert not room.stopped.exists()


def test_a_slot_cleaned_up_to_room_takes_gradings_again_at_once(
    machine: ModuleType, room: SimpleNamespace
) -> None:
    room.free = [2**30, 10 * 2**30]

    machine.check_room(GRADER, "small-1")

    assert room.done.index(STOP) < room.done.index(PRUNED[0]) < room.done.index(START)
    assert not room.stopped.exists()


def test_the_low_disk_check_leaves_alone_a_slot_set_up_or_not_mounted(
    machine: ModuleType, room: SimpleNamespace
) -> None:
    """A runner not enabled is one setup disabled, or whose self-test
    failed: starting it would take gradings on a machine not checked."""
    room.free = [2**30]
    room.enabled = "disabled"
    machine.check_room(GRADER, "small-1")
    room.enabled, room.mounted = "enabled", False
    machine.check_room(GRADER, "small-1")

    assert [c[:2] for c in room.done] == [("systemctl", "is-enabled")]
    assert room.free == [2**30]


def test_the_low_disk_check_looks_at_every_slot_one_failing(
    machine: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    looked: list[str] = []

    def check_room(grader: Any, slot: str) -> None:
        assert grader.pw_name == "unicon-grader"
        looked.append(slot)
        if slot == "small-1":
            raise machine.MachineError("systemctl start exited 1")

    users = {
        "unicon-grader": SimpleNamespace(pw_name="unicon-grader", pw_uid=1001),
        "root": SimpleNamespace(pw_name="root", pw_uid=0),
    }
    monkeypatch.setattr(machine, "check_room", check_room)
    monkeypatch.setattr(machine.os, "geteuid", lambda: 0)
    monkeypatch.setattr(machine.pwd, "getpwnam", users.__getitem__)

    assert (
        machine.main(["room", "--grader", "unicon-grader", "small-1", "small-2"]) == 1
    )
    assert looked == ["small-1", "small-2"]
    # Root's daemon is not the throwaway user's.
    assert machine.main(["room", "--grader", "root", "small-1"]) == 2
    assert machine.main(["room", "--grader", "nobody-here", "small-1"]) == 2
    assert looked == ["small-1", "small-2"]


def test_the_low_disk_check_drives_a_daemon_as_the_throwaway_user(
    machine: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    done: list[tuple[str, ...]] = []

    def run(*command: str, **_: object) -> subprocess.CompletedProcess[str]:
        done.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(machine, "run", run)

    machine.docker_as(GRADER, "small-1", "container", "prune", "--force")

    (command,) = done
    assert command[:4] == ("runuser", "-u", "unicon-grader", "--")
    assert command[-6:] == (
        "docker",
        "-H",
        "unix:///run/unicon-docker/small-1.sock",
        "container",
        "prune",
        "--force",
    )
