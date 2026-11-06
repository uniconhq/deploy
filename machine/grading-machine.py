#!/usr/bin/env python3
"""Make a machine a grading machine, and check that it is one.

Run as root on Ubuntu 24.04 at enrolment, with the machine's settings in a
JSON file (see `Settings`):

    python3 grading-machine.py setup /etc/unicon/machine.json
    python3 grading-machine.py self-test /etc/unicon/machine.json

It builds one shape, provisionally: rootless Docker under a throwaway
user, and nothing else takes a step. Ubuntu 24.04 is the one system it was
run on; another is allowed, with a warning, when the self-test passes
there. Every setting is checked, this machine's architecture found in
`agent_binary_sha256`, its slots worked out (`plan_slots`), and the room
for their disks found on the machine's own disk, before anything on the
machine is changed. A slot is a share of the machine of one size of
`slots.json`, kept beside this script, the one table of sizes the forge
plans each pipeline into too: cores of its own, a memory cap and a disk of
its own, run by a rootless daemon of its own and an agent of its own that
takes one workflow at a time. Everything a grading writes, its images'
layers, its containers' own and its workspace, is on its slot's disk, so
the kernel stops it at the disk's size. The machine's cache is on a disk
of its own, shared by every slot. The slots are fixed until setup runs
again. Then, in order, `setup`:

1. disables the low-disk check and every agent, so nothing new is graded
   while the machine is set up;
2. makes the throwaway user, which owns every slot's rootless daemon,
   lingering on so its daemons run without a login, with cpu, cpuset, io,
   memory and pids delegated to every user's systemd, and the agent's own
   user, a member of the throwaway user's group so it reaches the
   daemons' sockets and nothing of the throwaway user's traffic is its
   own;
3. kills every container of every daemon of the throwaway user's and
   stops the daemons, since a step's container outlives its agent, and
   stops there, the wall still up, if a daemon still answers; only then
   takes the wall down; and disables the machine's root Docker, whose
   `docker` group is root on the machine and whose containers no wall
   holds;
4. allows `rootlesskit` its user namespace where AppArmor restricts them,
   as Ubuntu 24.04 does;
5. makes each slot's disk and the cache's, once, and mounts them at every
   boot before the throwaway user's systemd starts (`make_disks`), and
   removes the disks of slots gone;
6. counts out-of-memory kills in each cgroup alone (`memory_localevents`),
   at every boot before the throwaway user's systemd starts, so a box's
   kill does not mark the step container killed, which Woodpecker would
   fail the step for;
7. writes `/etc/unicon/hosts`, the names a step sees: the forge's and
   Garage's addresses and no other, and pins every name the daemons and
   the agents use in `/etc/hosts`, since under the wall no lookup of the
   throwaway user's is answered; and `/etc/unicon/cache-cap`, the most the
   step cache may hold;
8. gives each slot's daemon its own daemon.json, and every daemon the one
   unit (`daemon_unit`), written as the throwaway user, whose folder it
   is: `"dns"` at an address that answers nothing, so a container's
   lookup goes nowhere, the data root on its slot's disk, a runtime
   folder and the socket the slot's agent reaches, and any registry it
   may pull from over plain HTTP;
9. starts every slot's daemon, since rootlesskit copies `/etc` as each
   starts, pulls the images named in the settings into each while the
   machine can still reach them, one copy per slot, and installs the
   agent's binary from its pinned image once its SHA-256 is the one the
   settings give;
10. puts the wall up: an nftables `inet` table on the throwaway user's
    uid, which holds for IPv4 and IPv6 alike and for every daemon,
    letting its traffic reach only the addresses the settings allow,
    loaded at every boot before the user's systemd starts, and again
    whenever `nftables.service`, whose rules file flushes every table, is
    restarted or reloaded;
11. writes each slot's agent's environment, removing those of slots gone,
    caps the throwaway user's slice, which holds every daemon and every
    container, at the machine's memory less what the system and the
    agents keep (`slice_drop_in`), and installs the low-disk check
    (`check_room`), a copy of this script that a timer runs every minute;
12. runs the self-test, and only when it passes enables every slot's
    agent: as its own user, against its slot's daemon, starting only
    while the wall is loaded and its slot's disk and the cache's are
    mounted, with `/etc/unicon/hosts` as every step's `/etc/hosts`, the
    cache's cap, its slot's cores and memory on every container, and its
    pool, shape and size labels; and enables the low-disk check.

Run again, it does the same, so it is how a machine takes new settings,
and how the pinned addresses follow the registry's when they change. A
disk it made before is kept as it is, its data with it, unless its size
changed, when it is made again empty.

`self-test` checks that every slot's daemon answers, rootless on cgroup v2
under systemd, with its data root on its slot's disk; then, from a
container of the first slot's daemon on a network made as Woodpecker
makes one per pipeline: that it reaches the first address the wall
allows; that it reaches none of the outside addresses in the settings,
over IPv4 or IPv6, and none of the machine's own; and that a lookup gets
no answer. Then, as the throwaway user in the machine's own network
namespace, where the daemons' pulls leave: the outside addresses, the
machine's resolver at 127.0.0.53, and its loopback ports. Each target is
tried from root first, which the wall does not hold: one root does not
reach either is "not tested", not passed, and so is one a step has no
route to, since then no wall refused it; at least one outside address must
be tested. Then that a container's memory and process limits bite, which a
box's limits need; that the cgroup hierarchy kept every option it was
remounted with; that a container over its own memory is marked
killed and one whose child cgroup was killed is not; that a privileged
container, as an exec step is, can open no block device; that the
controllers are delegated; that the root Docker is stopped; the kernel
settings a box's syscall filter leaves to the machine; and that the slice
is capped. The daemons are made alike, by the same functions, so those
are tried on the first slot's alone. For each slot, on its own daemon:
that a container made with its limits has its cores alone, its memory and
no swap; that its disk is fully allocated on the machine's disk; and that
a container writing past the disk gets `ENOSPC` while the other slots'
disks stay as they were, "not tested" while any agent runs, as on a
machine that grades. And that a folder of the cache's disk is made on
first use, and that what one slot writes to it, another reads. It prints
"network wall: on" or "network wall: off", and exits 1 when any check
fails.

`room`, which the timer runs as root with the throwaway user's name and
the slots', is the low-disk check (`check_room`).

Run on a real Ubuntu 24.04 (arm64, in a VM) and on a stand-in, an Ubuntu
24.04 container booting systemd over Docker Desktop's kernel. The
production enrolment (the worker, feature 12) is to run this script. It
stays within Python 3.12's standard library, the one Ubuntu 24.04 ships.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import ipaddress
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SHAPES = ("rootless",)
DELEGATED = ("cpu", "cpuset", "io", "memory", "pids")
WALL_TABLE = "unicon-wall"
NOWHERE = "192.0.2.1"
SOCKET_DIRECTORY = Path("/run/unicon-docker")
"""Every slot's daemon's socket the agents reach, `<slot>.sock`."""
SOCKET_TMPFILES = Path("/etc/tmpfiles.d/unicon-docker.conf")
DISKS = Path("/var/lib/unicon")
"""Where setup keeps the disks it makes, each a file of a fixed size on
the machine's own disk, mounted beside it."""
SLOT_DISKS = DISKS / "slots"
"""Each slot's disk, `<slot>.img`, mounted at `<slot>`, its daemon's data
root."""
CACHE_DISK = DISKS / "cache"
"""The cache's disk, `cache.img`, mounted here. The forge mounts the
folder of the grading's org on it, `<org>`, at every step's `/cache`."""
CACHE_FOLDER_TEST = CACHE_DISK / ".self-test"
"""The self-test's folder on the cache's disk, a name no org can have."""
FSTAB = Path("/etc/fstab")
FSTAB_BEGIN = "# unicon disks: begin"
FSTAB_END = "# unicon disks: end"
DAEMON_UNIT = "unicon-docker@.service"
"""Every slot's daemon, a user unit of the throwaway user's, an instance
by the slot's name."""
ROOM_UNIT = "unicon-slot-room.service"
ROOM_TIMER = "unicon-slot-room.timer"
ROOM_SCRIPT = Path("/usr/local/lib/unicon/grading-machine.py")
"""The copy of this script the low-disk check runs, installed by setup."""
ROOM_STATE = Path("/run/unicon/slot-room")
"""`<slot>.stopped` while the low-disk check holds that slot's agent
stopped."""
ROOM_LEAST = 2 * 2**30
"""The least free space, in bytes, of a slot not nearly full."""
WORKFLOW_LONGEST = 60 * 60
"""The longest a workflow runs, in seconds: the CI server's
`WOODPECKER_MAX_PIPELINE_TIMEOUT` (`compose.yaml`), 60 minutes, which
no pipeline's own timeout passes. A workflow's network older than this
is one its agent, stopped, could not remove."""
UNICON = Path("/etc/unicon")
STEP_HOSTS = UNICON / "hosts"
CACHE_CAP = UNICON / "cache-cap"
WALL_FILE = UNICON / "wall.nft"
OLD_AGENT_ENV = UNICON / "agent.env"
"""The one agent's environment before the machine ran one agent per slot,
removed at setup, as its token is in it."""
AGENT_BINARY = Path("/usr/local/bin/woodpecker-agent")
AGENT_STATE = Path("/var/lib/unicon-agent")
UNITS = Path("/etc/systemd/system")
OLD_AGENT = "unicon-agent.service"
"""The one agent's unit before the machine ran one agent per slot."""
AGENT_UNIT = "unicon-agent@.service"
"""Every slot's agent, an instance of this unit by the slot's name."""
SLOT_TABLE = Path(__file__).resolve().with_name("slots.json")
"""The slot sizes, deploy's one table of them, which the forge plans with
too (`UNICON_SLOT_SIZES`, written by bootstrap): kept beside this script."""
SYSTEM_MB = 1024
"""What a machine keeps of its memory, in MB, outside the throwaway user's
slice, which every container is in: the system and the agents."""
DAEMON_MB = 256
"""What the slice keeps beside each slot, in MB, for the slot's own
daemon: dockerd, containerd and rootlesskit with its network helper, about
215 MB on Ubuntu 24.04."""
SYSTEM_CORES = 1
"""The machine's first cores, kept out of every slot for the system, the
agents and the daemon."""
FIRST_HEALTH_PORT = 3000
"""The loopback port of the first slot's agent's health check, the next
slot's the next port."""
SLOT_VARIABLES = (
    "WOODPECKER_MAX_WORKFLOWS",
    "WOODPECKER_AGENT_LABELS",
    "WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_SET",
    "WOODPECKER_BACKEND_DOCKER_LIMIT_MEM",
    "WOODPECKER_BACKEND_DOCKER_LIMIT_MEM_SWAP",
)
"""The agent's variables a slot is made of, which `agent_environment`
may not change."""
CGROUP_UNIT = UNITS / "unicon-cgroup.service"
HOSTS = Path("/etc/hosts")
CGROUP = Path("/sys/fs/cgroup")
MOUNTINFO = Path("/proc/self/mountinfo")
ONLINE_CPUS = Path("/sys/devices/system/cpu/online")
LOCAL_EVENTS = "memory_localevents"
ROOT_DOCKER = ("docker.socket", "docker.service")
HOSTS_BEGIN = "# unicon machine names: begin"
HOSTS_END = "# unicon machine names: end"
APPARMOR_SWITCH = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
APPARMOR_PROFILE = Path("/etc/apparmor.d/unicon-rootlesskit")
TESTED_ON = ("ubuntu", "24.04")
REQUIRED_TOOLS = (
    "nft",
    "systemctl",
    "loginctl",
    "runuser",
    "newuidmap",
    "dockerd-rootless-setuptool.sh",
    "dockerd-rootless.sh",
    "docker",
    "fallocate",
    "mkfs.ext4",
)
# A host name as /etc/hosts takes one: labels of letters, digits and
# hyphens, dot separated.
HOST_NAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
SHA256 = re.compile(r"[0-9a-f]{64}")
ARCHITECTURES = ("x86_64", "aarch64")
"""The architectures `agent_binary_sha256` may name, as `uname -m` does."""
USER_NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
NO_LOGIN = ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false")
FIRST_PERSON_UID = 1000
"""The first uid `useradd` gives a user it is not told is a system user,
as Ubuntu and Debian set it: setup makes the throwaway user one of these,
and the agent's user a system user, below."""
# An agent label's value: the agent reads its labels as `key=value` pairs
# split on commas.
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# The kernel settings a box's own filter leaves open, with the least each
# may be: unprivileged BPF refused (Ubuntu's default is 2), perf events
# refused to every unprivileged user (Ubuntu's 4; 3 and up is Debian's and
# Ubuntu's patch), userfaultfd for user-mode faults only (the default, 0)
# or not built at all.
KERNEL = (
    ("kernel.unprivileged_bpf_disabled", 1, "unprivileged BPF refused"),
    ("kernel.perf_event_paranoid", 3, "no perf events for unprivileged users"),
)
USERFAULTFD = "vm.unprivileged_userfaultfd"
# What a probe gets when nothing it sent left the step, so no wall
# refused it: no route, or no such address family.
NO_ROUTE = ("ENETUNREACH", "EADDRNOTAVAIL", "EAFNOSUPPORT")


class MachineError(Exception):
    """A setting or a step that leaves the machine unfit to grade."""


@dataclass(frozen=True)
class Target:
    """An address and a TCP port, IPv4 or IPv6."""

    address: str
    port: int

    @classmethod
    def parse(cls, text: str) -> Target:
        """`1.2.3.4:80` or `[fd41::40]:80`: an IPv6 address in brackets, so
        its last group is never read as the port."""
        if text.startswith("["):
            address, bracket, port = text[1:].partition("]:")
        else:
            address, bracket, port = text.rpartition(":")
        try:
            parsed = ipaddress.ip_address(address)
            number = int(port)
        except ValueError as error:
            raise MachineError(f"{text!r} is not an address and a port") from error
        if not bracket or (parsed.version == 6) != text.startswith("["):
            raise MachineError(
                f"{text!r} is not an address and a port; write an IPv6 address "
                "in brackets, as [2001:db8::1]:443"
            )
        if not 1 <= number <= 65535:
            raise MachineError(f"{text!r} has no port from 1 to 65535")
        return cls(address, number)

    @property
    def family(self) -> str:
        return "ip6" if ":" in self.address else "ip"

    def __str__(self) -> str:
        if self.family == "ip6":
            return f"[{self.address}]:{self.port}"
        return f"{self.address}:{self.port}"


@dataclass(frozen=True)
class Size:
    """A slot size of `SLOT_TABLE`: its name, the cores a slot of it is
    pinned to, the memory every container of its workflow is capped at,
    in MB, and the size of its disk, in GiB."""

    name: str
    cores: int
    memory_mb: int
    disk_gb: int


@dataclass(frozen=True)
class Disk:
    """A disk setup makes: a file of `size` bytes on the machine's own disk,
    `image`, formatted ext4 and loop-mounted beside it at every boot."""

    image: Path
    size: int

    @property
    def mount(self) -> Path:
        """Where it is mounted: beside its file, by its name."""
        return self.image.with_suffix("")


@dataclass(frozen=True)
class Slot:
    """One slot of the machine and its agent: its name, `<size>-<n>`, its
    size, the cores it is pinned to as cpuset names them, its agent's
    health check port, and whether it takes a pipeline of any size, as a
    slot of the largest size does. Every slot takes any grading, first
    come, first served."""

    name: str
    size: Size
    cpus: str
    port: int
    any_size: bool = False

    @property
    def memory(self) -> int:
        """Its memory in bytes."""
        return self.size.memory_mb * 2**20

    @property
    def disk(self) -> Disk:
        """Its disk, its daemon's data root."""
        return Disk(SLOT_DISKS / f"{self.name}.img", self.size.disk_gb * 2**30)

    def labels(self, settings: Settings) -> str:
        """Its agent's labels. The forge labels a pipeline with the size it
        fits. A slot of the largest size takes a pipeline of any size, `*`,
        so smaller work overflows into it; Woodpecker gives a pipeline to
        the agent whose labels match it most exactly, so smaller work goes
        to a slot of its own size first."""
        size = "*" if self.any_size else self.size.name
        return f"{settings.labels},size={size}"


def read_sizes(text: str) -> tuple[Size, ...]:
    """The sizes of `SLOT_TABLE`, `{name: {cores, memory_mb, disk_gb}}`,
    smallest first."""
    try:
        table = json.loads(text)
    except json.JSONDecodeError as error:
        raise MachineError(f"{SLOT_TABLE} is not JSON: {error}") from error
    if not isinstance(table, dict) or not table:
        raise MachineError(f"{SLOT_TABLE} names no slot size")
    sizes = []
    for name, size in table.items():
        if (
            not LABEL.fullmatch(name)
            or not isinstance(size, dict)
            or set(size) != {"cores", "memory_mb", "disk_gb"}
            or not all(whole(value) and value >= 1 for value in size.values())
        ):
            raise MachineError(
                f"{SLOT_TABLE} gives {name!r} no whole cores, memory_mb and "
                "disk_gb, at least 1"
            )
        sizes.append(Size(name, size["cores"], size["memory_mb"], size["disk_gb"]))
    return tuple(sorted(sizes, key=lambda size: size.memory_mb))


def read_cpu_list(text: str) -> list[int]:
    """CPUs as the kernel lists them, `0-3,6`, one by one."""
    cpus: list[int] = []
    for part in text.strip().split(","):
        first, _, last = part.partition("-")
        cpus += range(int(first), int(last or first) + 1)
    return sorted(cpus)


def cpu_list(cpus: Sequence[int]) -> str:
    """CPUs as cpuset names them, runs as ranges: `1-4,6`."""
    runs: list[list[int]] = []
    for cpu in sorted(cpus):
        if runs and cpu == runs[-1][-1] + 1:
            runs[-1].append(cpu)
        else:
            runs.append([cpu])
    return ",".join(
        f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]) for run in runs
    )


def plan_slots(
    sizes: Sequence[Size],
    settings: Settings,
    cpus: Sequence[int],
    memory_mb: int,
    disk_bytes: int,
) -> tuple[Slot, ...]:
    """The machine's slots, fixed until setup runs again, from its `cpus`
    and `memory_mb`, less `SYSTEM_CORES` and `SYSTEM_MB`, each slot
    holding its memory and `DAEMON_MB` for its daemon, and from the
    `disk_bytes` its disk has for the disks setup makes (`disks_room`),
    less the cache's.
    `settings.slots` gives how many of each size; otherwise, of each size
    above the smallest, the largest first, one while at least two of the
    smallest still fit beside it, and then as many of the smallest as fit,
    so a small machine runs small slots alone. Each slot gets cores of its
    own.
    Refused when the slots asked for do not fit, or none does, naming the
    smallest machine one slot fits on.
    """
    free_cpus = sorted(cpus)[SYSTEM_CORES:]
    free_mb = memory_mb - SYSTEM_MB
    free_disk = disk_bytes - settings.cache_gb * 2**30
    by_name = {size.name: size for size in sizes}
    unknown = sorted(set(settings.slots) - set(by_name))
    if unknown:
        raise MachineError(
            f"slots names {', '.join(unknown)}, which {SLOT_TABLE.name} does not: "
            f"{', '.join(by_name)}"
        )
    smallest = sizes[0]

    def holds(size: Size) -> int:
        return size.memory_mb + DAEMON_MB

    def fills(size: Size) -> int:
        return size.disk_gb * 2**30

    def fitting(size: Size, cores: int, held: int, filled: int) -> int:
        return max(
            0,
            min(
                (len(free_cpus) - cores) // size.cores,
                (free_mb - held) // holds(size),
                (free_disk - filled) // fills(size),
            ),
        )

    counts = {size.name: settings.slots.get(size.name, 0) for size in sizes}
    if not settings.slots:
        cores, held, filled = 0, 0, 0
        for size in reversed(sizes[1:]):
            more = (cores + size.cores, held + holds(size), filled + fills(size))
            if fitting(smallest, *more) >= 2:
                counts[size.name] = 1
                cores, held, filled = more
        counts[smallest.name] = fitting(smallest, cores, held, filled)
    cores = sum(by_name[name].cores * count for name, count in counts.items())
    held = sum(holds(by_name[name]) * count for name, count in counts.items())
    filled = sum(fills(by_name[name]) * count for name, count in counts.items())
    if (
        not any(counts.values())
        or cores > len(free_cpus)
        or held > free_mb
        or filled > free_disk
    ):
        asked = ", ".join(f"{n} {name}" for name, n in counts.items())
        raise MachineError(
            (
                f"the slots ({asked}) need {cores} cores, {held} MB and "
                f"{filled // 2**30} GiB of disk"
                if any(counts.values())
                else "no slot fits"
            )
            + f"; this machine gives slots {len(free_cpus)} cores, {free_mb} MB "
            f"and {max(0, free_disk) / 2**30:.1f} GiB of disk beside the cache's. "
            f"The smallest machine a {smallest.name} slot fits on has "
            f"{SYSTEM_CORES + smallest.cores} cores, "
            f"{SYSTEM_MB + holds(smallest)} MB of memory (MemTotal) and "
            f"{smallest.disk_gb + settings.cache_gb} GiB free on its disk under "
            f"{DISKS}, for the slot's disk and the cache's (cache_gb)"
        )
    slots: list[Slot] = []
    for size in sizes:
        for number in range(1, counts[size.name] + 1):
            taken, free_cpus = free_cpus[: size.cores], free_cpus[size.cores :]
            slots.append(
                Slot(
                    f"{size.name}-{number}",
                    size,
                    cpu_list(taken),
                    FIRST_HEALTH_PORT + len(slots),
                    size == sizes[-1],
                )
            )
    return tuple(slots)


@dataclass(frozen=True)
class Settings:
    """What the machine is told at enrolment. `server`, `agent_secret_file`,
    `agent_image`, `agent_binary_sha256`, `step_hosts`, `wall_allow` and
    `test_image` have no default.

    `agent_binary_sha256` is the SHA-256 of `/bin/woodpecker-agent` in
    `agent_image`, by the machine's architecture as `uname -m` names it,
    `x86_64` or `aarch64`: the binary is copied out of an image the
    throwaway user's daemon holds, so it runs only as pinned. `test_image`,
    which the self-test runs privileged, is pinned by its digest, as
    `agent_image` is: an image index's digest, or one of an image for the
    machine's architecture, since the self-test runs it there.
    `grader_user` and `agent_user` differ, and each is a user setup makes,
    or made before (`taken`): not root, a user who can log in, or a system
    user of another's. `pool` is a label's value: letters,
    digits, `.`, `_` and `-`.
    `step_hosts` is every step's `/etc/hosts`: the forge's and Garage's
    names and addresses. `machine_hosts` is pinned in the machine's own
    `/etc/hosts`: every name the daemon pulls from and the agent dials.
    `wall_allow` is every address and port the throwaway user's traffic
    may reach, the step's and the daemon's alike; the first is the one the
    self-test expects a step to reach. `outside` is where the self-test
    expects a step not to get, and the machine itself to reach. `pull` is
    pulled at setup, before the wall goes up: the test image, and whatever
    a pipeline runs that the wall keeps the daemon from pulling later.
    `cache_gb` is the size of the cache's disk, in GiB, which every slot
    shares. `cache_cap` is the most the step cache may hold, in bytes, no
    more than the disk's filesystem (`cache_cap`), a little less than
    `cache_gb`; 90 % of that filesystem when not given.
    `slots` gives how many slots
    of each size of `SLOT_TABLE` the machine runs (`plan_slots`);
    the sizes themselves are the table's alone, which the forge plans
    with.
    `agent_environment` is passed to every agent as it is, after the
    variables this script sets, but for those a slot is made of.
    """

    server: str
    agent_secret_file: Path
    agent_image: str
    agent_binary_sha256: dict[str, str]
    step_hosts: dict[str, str]
    wall_allow: tuple[Target, ...]
    test_image: str
    shape: str = "rootless"
    pool: str = "platform"
    grader_user: str = "unicon-grader"
    agent_user: str = "unicon-agent"
    machine_hosts: dict[str, str] = field(default_factory=dict)
    outside: tuple[Target, ...] = (
        Target("1.1.1.1", 443),
        Target("2606:4700:4700::1111", 443),
    )
    pull: tuple[str, ...] = ()
    insecure_registries: tuple[str, ...] = ()
    cache_gb: int = 20
    cache_cap: int | None = None
    slots: dict[str, int] = field(default_factory=dict)
    agent_environment: dict[str, str] = field(default_factory=dict)

    @classmethod
    def read(cls, path: Path) -> Settings:
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise MachineError(f"cannot read the settings {path}: {error}") from error
        if not isinstance(raw, dict):
            raise MachineError(f"{path} does not hold an object")
        return cls.of(raw)

    @classmethod
    def of(cls, raw: dict[str, object]) -> Settings:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(raw) - known)
        if unknown:
            raise MachineError(f"unknown settings: {', '.join(unknown)}")
        missing = sorted(
            name
            for name in (
                "server",
                "agent_secret_file",
                "agent_image",
                "agent_binary_sha256",
                "step_hosts",
                "wall_allow",
                "test_image",
            )
            if name not in raw
        )
        if missing:
            raise MachineError(f"missing settings: {', '.join(missing)}")
        values = dict(raw)
        if not isinstance(values["agent_secret_file"], str):
            raise MachineError("agent_secret_file is a path, as a text")
        values["agent_secret_file"] = Path(values["agent_secret_file"])
        for name in ("wall_allow", "outside"):
            if name in values:
                values[name] = tuple(Target.parse(t) for t in _texts(values, name))
        for name in ("pull", "insecure_registries"):
            if name in values:
                values[name] = tuple(_texts(values, name))
        settings = cls(**values)  # type: ignore[arg-type]
        settings.check()
        return settings

    def check(self) -> None:
        """Refuse settings of the wrong type or that would leave the machine
        unfit to grade, all before setup changes anything."""
        for name in (
            "server",
            "agent_image",
            "test_image",
            "shape",
            "pool",
            "grader_user",
            "agent_user",
        ):
            if not isinstance(getattr(self, name), str):
                raise MachineError(f"{name} is a text")
        for name in (
            "agent_binary_sha256",
            "step_hosts",
            "machine_hosts",
            "agent_environment",
        ):
            value = getattr(self, name)
            if not isinstance(value, dict) or not all(
                isinstance(key, str) and isinstance(item, str)
                for key, item in value.items()
            ):
                raise MachineError(f"{name} is an object of texts")
        if self.shape not in SHAPES:
            raise MachineError(
                f"shape {self.shape!r} is not built; the one shape is rootless"
            )
        if not LABEL.fullmatch(self.pool):
            raise MachineError(
                f"pool {self.pool!r} is not a label's value: letters, digits, "
                "'.', '_' and '-'"
            )
        if not self.wall_allow:
            raise MachineError("wall_allow is empty: a step could reach nothing")
        if self.grader_user == self.agent_user:
            raise MachineError("grader_user and agent_user must differ")
        for name, home, system in (
            ("grader_user", Path("/home") / self.grader_user, False),
            ("agent_user", AGENT_STATE, True),
        ):
            user = getattr(self, name)
            if (
                not USER_NAME.fullmatch(user)
                or user == "root"
                or taken(user, home, system=system)
            ):
                raise MachineError(
                    f"{name} {user!r} is root, a user setup did not make, or not "
                    "a user name; name a user of its own, made by setup"
                )
        if "@sha256:" not in self.agent_image:
            raise MachineError("agent_image is pinned by its digest")
        if "@sha256:" not in self.test_image:
            raise MachineError(
                "test_image is pinned by its digest: the self-test runs it privileged"
            )
        if (
            not self.agent_binary_sha256
            or not set(self.agent_binary_sha256) <= set(ARCHITECTURES)
            or not all(map(SHA256.fullmatch, self.agent_binary_sha256.values()))
        ):
            raise MachineError(
                "agent_binary_sha256 gives the agent binary's SHA-256 in hex for "
                f"{' or '.join(ARCHITECTURES)}, or both"
            )
        if not isinstance(self.slots, dict) or not all(
            isinstance(name, str) and whole(count) and count >= 0
            for name, count in self.slots.items()
        ):
            raise MachineError("slots gives each size a whole number of slots")
        if not whole(self.cache_gb) or self.cache_gb < 1:
            raise MachineError("cache_gb is a whole number of GiB, at least 1")
        if self.cache_cap is not None and (
            not whole(self.cache_cap) or self.cache_cap < 2**30
        ):
            raise MachineError("cache_cap is a number of bytes, at least 1 GiB")
        if self.cache_cap is not None and self.cache_cap > self.cache_gb * 2**30:
            raise MachineError(
                f"cache_cap is more than the cache's disk of {self.cache_gb} GiB "
                "(cache_gb)"
            )
        for name in self.agent_environment:
            if not ENVIRONMENT_NAME.fullmatch(name):
                raise MachineError(f"agent_environment names {name!r}")
            if name in SLOT_VARIABLES:
                raise MachineError(
                    f"agent_environment sets {name}, which the agent's slot sets"
                )
        for hosts in (self.step_hosts, self.machine_hosts):
            for name, address in hosts.items():
                if not HOST_NAME.fullmatch(name):
                    raise MachineError(f"{name!r} is not a host name")
                try:
                    ipaddress.ip_address(address)
                except ValueError as error:
                    raise MachineError(
                        f"{name} is pinned to {address!r}, not an address"
                    ) from error

    @property
    def labels(self) -> str:
        return f"pool={self.pool},shape={self.shape}"


def _texts(values: dict[str, object], name: str) -> list[str]:
    value = values[name]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise MachineError(f"{name} is a list of texts")
    return value


def whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def taken(user: str, home: Path, *, system: bool) -> bool:
    """Whether `user` is a user of the machine already that setup did not
    make: root, one whose shell lets them log in, one whose home is not
    `home`, the one setup gives it (`make_users`), or one whose uid is not
    of the kind setup gives it, a system user's, below `FIRST_PERSON_UID`,
    when `system`, and otherwise one from it on. Setup puts the agent's
    user in the throwaway user's group and walls the throwaway user's
    traffic, which is for users of their own: a system user such as
    `nobody` or `systemd-resolve` taken for the throwaway user would have
    its traffic walled, the machine's names with it."""
    try:
        found = pwd.getpwnam(user)
    except KeyError:
        return False
    return (
        found.pw_uid == 0
        or found.pw_shell not in NO_LOGIN
        or Path(found.pw_dir) != home
        or (found.pw_uid < FIRST_PERSON_UID) != system
    )


def architecture() -> str:
    return os.uname().machine


# What is written. Each is a function of the settings alone, so a test can
# read it without a machine.


def wall_table(uid: int, allowed: Sequence[Target]) -> str:
    """The wall as an nftables script that replaces any earlier one: the
    throwaway user's new connections go only to the allowed addresses and
    ports, over TCP, and everything else of its is refused. Every other
    user's traffic passes, the agent's included.

    The containers' traffic leaves through the network helper, slirp4netns
    or pasta, and the daemon's own pulls from the machine's own network
    namespace, since Docker 29 starts rootlesskit with `--detach-netns`;
    both as the throwaway user, so both meet this table, and the
    registry's addresses must be among the allowed.
    """
    rules = [
        f"    {t.family} daddr {t.address} tcp dport {t.port} accept" for t in allowed
    ]
    return "\n".join(
        [
            f"table inet {WALL_TABLE}",
            f"delete table inet {WALL_TABLE}",
            f"table inet {WALL_TABLE} {{",
            "  chain output {",
            "    type filter hook output priority filter; policy accept;",
            f"    meta skuid != {uid} accept",
            "    ct state established,related accept",
            *rules,
            "    reject",
            "  }",
            "}",
            "",
        ]
    )


def step_hosts(names: dict[str, str]) -> str:
    """Every step's `/etc/hosts`."""
    lines = ["127.0.0.1 localhost", "::1 localhost ip6-localhost ip6-loopback"]
    lines += [f"{address} {name}" for name, address in sorted(names.items())]
    return "\n".join(lines) + "\n"


def with_block(current: str, begin: str, end: str, lines: Sequence[str]) -> str:
    """`current` with our block, from `begin` to `end`, replaced by
    `lines`, the rest left as it was."""
    kept: list[str] = []
    inside = False
    for line in current.splitlines():
        if line == begin:
            inside = True
        elif line == end:
            inside = False
        elif not inside:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join([*kept, "", begin, *lines, end]) + "\n"


def pinned_hosts(current: str, names: dict[str, str]) -> str:
    """The machine's `/etc/hosts` with our block replaced by these names, the
    rest left as it was.
    """
    lines = [f"{address} {name}" for name, address in sorted(names.items())]
    return with_block(current, HOSTS_BEGIN, HOSTS_END, lines)


def fstab(current: str, disks: Sequence[Disk], uid: int) -> str:
    """The machine's `/etc/fstab` with our block replaced by these disks,
    the rest left as it was. Each is mounted before the throwaway user's
    systemd starts, which starts the daemons, and the boot goes on without
    one that fails, whose daemon then does not start (`daemon_unit`).
    `fstrim`, which Ubuntu runs every week, skips them: through the loop
    device it would give the freed blocks of the file back to the
    machine's disk, which could then fill first, and a step would get I/O
    errors rather than `ENOSPC`."""
    lines = [
        f"{disk.image} {disk.mount} ext4 "
        f"loop,nofail,X-fstrim.notrim,x-systemd.before=user@{uid}.service 0 2"
        for disk in disks
    ]
    return with_block(current, FSTAB_BEGIN, FSTAB_END, lines)


def runtime_folder(uid: int, slot: str) -> str:
    """The slot's daemon's own folder in the throwaway user's runtime
    folder, which every daemon would otherwise share: rootlesskit's state,
    the exec root, the pidfile and the default socket."""
    return f"/run/user/{uid}/unicon-docker-{slot}"


def daemon_socket(slot: str) -> Path:
    """The socket of the slot's daemon that its agent reaches."""
    return SOCKET_DIRECTORY / f"{slot}.sock"


def daemon_json(settings: Settings, uid: int, slot: str) -> str:
    """The daemon.json of the slot named `slot`: its data root on its disk,
    and its exec root, pidfile and sockets its own."""
    runtime = runtime_folder(uid, slot)
    document: dict[str, object] = {
        "dns": [NOWHERE],
        "data-root": str(SLOT_DISKS / slot),
        "exec-root": f"{runtime}/exec",
        "pidfile": f"{runtime}/docker.pid",
        # The sockets' group. The daemon's default is `docker`, whose id
        # inside its user namespace lands on a subordinate id outside, so
        # the agent could not reach the socket. Group 0 inside is the
        # throwaway user's own group outside, of which the agent's user
        # is a member.
        "group": "root",
        "hosts": [
            f"unix://{runtime}/docker.sock",
            f"unix://{daemon_socket(slot)}",
        ],
    }
    if settings.insecure_registries:
        document["insecure-registries"] = list(settings.insecure_registries)
    return json.dumps(document, indent=2) + "\n"


def daemon_unit() -> str:
    """Every slot's daemon, a user unit of the throwaway user's, an
    instance by the slot's name: the unit `dockerd-rootless-setuptool.sh`
    writes, run with the slot's daemon.json and rootlesskit's state in the
    slot's runtime folder. It starts only while its slot's disk and the
    cache's are mounted, so it never writes onto the machine's disk under
    a bare mount point, nor a step's cache.
    """
    tool = shutil.which("dockerd-rootless.sh") or "/usr/bin/dockerd-rootless.sh"
    runtime = "%t/unicon-docker-%i"
    return (
        "[Unit]\n"
        "Description=Unicon rootless Docker of slot %i\n"
        "Requires=dbus.socket\n"
        f"ConditionPathIsMountPoint={SLOT_DISKS}/%i\n"
        f"ConditionPathIsMountPoint={CACHE_DISK}\n\n"
        "[Service]\n"
        f"Environment=PATH={Path(tool).parent}:/sbin:/usr/sbin:/bin\n"
        f"Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_STATE_DIR={runtime}/rootlesskit\n"
        f"ExecStartPre=/usr/bin/mkdir -p -m 0700 {runtime}\n"
        f"ExecStart={tool} --config-file %h/.config/docker/daemon-%i.json\n"
        "ExecReload=/bin/kill -s HUP $MAINPID\n"
        "TimeoutSec=0\n"
        "RestartSec=2\n"
        "Restart=always\n"
        "StartLimitBurst=3\n"
        "StartLimitInterval=60s\n"
        "LimitNOFILE=infinity\n"
        "LimitNPROC=infinity\n"
        "LimitCORE=infinity\n"
        "TasksMax=infinity\n"
        "Delegate=yes\n"
        "Type=notify\n"
        "NotifyAccess=all\n"
        "KillMode=mixed\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def agent_environment(settings: Settings, secret: str, slot: Slot) -> str:
    """A slot's agent's environment file. Woodpecker reads the token from
    it, so it is written readable by root alone. The agent takes one
    workflow at a time from its slot's daemon, under its slot's labels, a
    name and a state folder of its own; every container of it is pinned to
    the slot's cores and
    capped at its memory, with no swap (Woodpecker 3.18's docker backend,
    `LIMIT_CPU_SET`, `LIMIT_MEM` and `LIMIT_MEM_SWAP`, on each container).
    Every step gets the machine's hosts file and the cache's cap,
    read-only.
    """
    volumes = [f"{STEP_HOSTS}:/etc/hosts:ro", f"{CACHE_CAP}:{CACHE_CAP}:ro"]
    values = {
        "WOODPECKER_SERVER": settings.server,
        "WOODPECKER_AGENT_SECRET": secret,
        "WOODPECKER_HOSTNAME": f"{socket.gethostname()}-{slot.name}",
        "WOODPECKER_AGENT_LABELS": slot.labels(settings),
        "WOODPECKER_AGENT_CONFIG_FILE": str(AGENT_STATE / slot.name / "agent.conf"),
        "WOODPECKER_MAX_WORKFLOWS": "1",
        "WOODPECKER_HEALTHCHECK_ADDR": f"127.0.0.1:{slot.port}",
        "WOODPECKER_BACKEND": "docker",
        "DOCKER_HOST": f"unix://{daemon_socket(slot.name)}",
        "WOODPECKER_BACKEND_DOCKER_VOLUMES": ",".join(volumes),
        "WOODPECKER_BACKEND_DOCKER_LIMIT_CPU_SET": slot.cpus,
        "WOODPECKER_BACKEND_DOCKER_LIMIT_MEM": str(slot.memory),
        "WOODPECKER_BACKEND_DOCKER_LIMIT_MEM_SWAP": str(slot.memory),
        **settings.agent_environment,
    }
    for name, value in values.items():
        if "\n" in value:
            raise MachineError(f"{name} holds a line break")
    return "".join(f"{name}={value}\n" for name, value in values.items())


def cache_cap(settings: Settings, cache_size: int) -> int:
    """The cache's cap: the setting, or 90 % of the cache's disk, its
    filesystem's `cache_size` bytes, which holds nothing else. Refused
    when the setting is more than the filesystem, a little less than
    `cache_gb`, which the settings' own check counts with."""
    if settings.cache_cap is not None and settings.cache_cap > cache_size:
        raise MachineError(
            f"cache_cap is more than the {cache_size} bytes of the cache's disk's "
            "filesystem"
        )
    return settings.cache_cap or cache_size * 9 // 10


def rootlesskit_profile() -> str:
    """Docker's own profile for an AppArmor that restricts user namespaces:
    `rootlesskit` may make one, and is otherwise unconfined.
    """
    return (
        "abi <abi/4.0>,\n"
        "include <tunables/global>\n\n"
        f'"{shutil.which("rootlesskit") or "/usr/bin/rootlesskit"}" '
        "flags=(unconfined) {\n"
        "  userns,\n"
        "  include if exists <local/unicon-rootlesskit>\n"
        "}\n"
    )


def wall_unit(uid: int) -> str:
    """The wall at boot: before the network and the throwaway user's
    systemd, and after `nftables.service`, whose rules file flushes every
    table. A restart of `nftables.service` restarts it after, and the agent
    with it, whose unit requires it; a reload loads it again after. Stopping
    the unit takes the wall down.
    """
    return (
        "[Unit]\n"
        "Description=Unicon grading wall\n"
        "DefaultDependencies=no\n"
        "After=nftables.service\n"
        "PartOf=nftables.service\n"
        "ReloadPropagatedFrom=nftables.service\n"
        f"Before=network-pre.target user@{uid}.service\n"
        "Wants=network-pre.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart=/usr/sbin/nft -f {WALL_FILE}\n"
        f"ExecReload=/usr/sbin/nft -f {WALL_FILE}\n"
        f"ExecStop=-/usr/sbin/nft delete table inet {WALL_TABLE}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def cgroup_mount(mountinfo: str) -> list[str]:
    """The cgroup2 hierarchy's options at `CGROUP`, from
    `/proc/self/mountinfo`: the mount's own, such as `nosuid`, then its
    superblock's, such as `nsdelegate`."""
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[4] == str(CGROUP) and " - cgroup2 " in line:
            superblock = line.split(" - ", 1)[1].split()[2]
            return fields[5].split(",") + superblock.split(",")
    raise MachineError(f"{CGROUP} is not a cgroup2 hierarchy")


def cgroup_options(mountinfo: str) -> str:
    """The options to remount the cgroup2 hierarchy with: those it has, the
    mount's and its superblock's, which a remount would otherwise clear,
    and `memory_localevents`. Only a process of the machine's own cgroup
    namespace changes them.
    """
    kept = [o for o in cgroup_mount(mountinfo) if o not in ("rw", "ro", LOCAL_EVENTS)]
    return ",".join(dict.fromkeys([*kept, LOCAL_EVENTS]))


def remounted_with(unit: str) -> list[str]:
    """The options `cgroup_unit` remounts the hierarchy with."""
    for line in unit.splitlines():
        if line.startswith("ExecStart=") and " -o remount," in line:
            return line.split(" -o remount,", 1)[1].split()[0].split(",")
    raise MachineError(f"{CGROUP_UNIT} remounts nothing")


def cgroup_unit(uid: int, options: str) -> str:
    """`memory_localevents` at boot, before the throwaway user's systemd, so
    a container's `memory.events` counts its own kills and not its
    children's: a box killed for its memory leaves exec's container
    unmarked, and Woodpecker does not fail the step. It fails when the
    option did not take on the hierarchy at `CGROUP`.
    """
    # The hierarchy's own line of mountinfo, its fifth field the mount point,
    # with the option among its superblock's; systemd reads `$$` as `$`.
    taken = f"^([^ ]+ ){{4}}{CGROUP} .* - cgroup2 [^ ]+ ([^ ]+,)?{LOCAL_EVENTS}(,|$$)"
    return (
        "[Unit]\n"
        "Description=Unicon: count memory kills in each cgroup alone\n"
        "DefaultDependencies=no\n"
        f"Before=user@{uid}.service\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart=/usr/bin/mount -o remount,{options} {CGROUP}\n"
        f"ExecStartPost=/usr/bin/grep -Eq '{taken}' /proc/self/mountinfo\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def agent_environment_file(slot: str) -> Path:
    """Where the agent of the slot named `slot` reads its environment."""
    return UNICON / f"agent-{slot}.env"


def agent_unit(settings: Settings, uid: int) -> str:
    """Every slot's agent, an instance by the slot's name, as its own user,
    starting only while the wall's table is loaded, once the cgroup option
    is set, and while its slot's disk and the cache's are mounted, as its
    daemon does, each with its own environment and state folder.
    """
    return (
        "[Unit]\n"
        "Description=Unicon grading agent of slot %i\n"
        f"After=network-online.target unicon-wall.service unicon-cgroup.service "
        f"user@{uid}.service\n"
        "Requires=unicon-wall.service unicon-cgroup.service\n"
        "Wants=network-online.target\n"
        f"ConditionPathIsMountPoint={SLOT_DISKS}/%i\n"
        f"ConditionPathIsMountPoint={CACHE_DISK}\n\n"
        "[Service]\n"
        f"User={settings.agent_user}\n"
        f"Group={settings.agent_user}\n"
        f"SupplementaryGroups={settings.grader_user}\n"
        f"EnvironmentFile={UNICON}/agent-%i.env\n"
        f"ExecStartPre=+/usr/sbin/nft list table inet {WALL_TABLE}\n"
        f"ExecStart={AGENT_BINARY}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "NoNewPrivileges=yes\n"
        "ProtectSystem=strict\n"
        "ProtectHome=yes\n"
        "PrivateTmp=yes\n"
        f"ReadWritePaths={AGENT_STATE}/%i\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def room_unit(grader_user: str, slots: Sequence[Slot]) -> str:
    """The low-disk check over every slot, from the copy of this script
    setup installs: as root, which stops and starts the agents, driving
    each daemon as `grader_user`, whose it is."""
    names = " ".join(slot.name for slot in slots)
    return (
        "[Unit]\n"
        "Description=Unicon: stop a nearly full slot's agent until its disk "
        "has room\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=/usr/bin/python3 -I {ROOM_SCRIPT} room --grader {grader_user} "
        f"{names}\n"
    )


def room_timer() -> str:
    """The low-disk check every minute."""
    return (
        "[Unit]\n"
        "Description=Unicon: check every slot's disk every minute\n\n"
        "[Timer]\n"
        "OnCalendar=minutely\n"
        "AccuracySec=5s\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def room_mark(size: int) -> int:
    """The free bytes below which a slot's disk of `size` bytes is nearly
    full: a tenth of it, or `ROOM_LEAST`, whichever is larger."""
    return max(size // 10, ROOM_LEAST)


def workflow_running(
    containers: str, networks: Sequence[dict[str, str]], now: float
) -> bool:
    """Whether a daemon runs a workflow: `containers`, the names of its
    running containers, one a line, has a workflow's (`wp_`), or
    `networks`, its networks as `docker network inspect` gives them, has
    a workflow's younger than `WORKFLOW_LONGEST` at `now`, as a workflow
    has between its steps and while it pulls its next step's image, when
    it runs no container. The names are matched here as well as by the
    daemon's filter, whose pattern is a regular expression."""
    if any(name.startswith("wp_") for name in containers.split()):
        return True
    return any(
        network["Name"].startswith("wp_")
        and now - created_at(network["Created"]) < WORKFLOW_LONGEST
        for network in networks
    )


def created_at(text: str) -> float:
    """The seconds since the epoch of a time Docker writes, RFC 3339 with
    up to nanoseconds, which Python reads only to microseconds."""
    try:
        return datetime.fromisoformat(
            re.sub(r"(\.\d{6})\d+", r"\1", text).replace("Z", "+00:00")
        ).timestamp()
    except ValueError as error:
        raise MachineError(f"the daemon gives a network's time as {text!r}") from error


def slice_memory(memory_mb: int) -> int:
    """The throwaway user's slice's cap, in bytes: the machine's memory less
    `SYSTEM_MB`. Every container of every slot is in it, with every slot's
    daemon, so all of them together leave the system and the agents their
    room whatever a step does."""
    return (memory_mb - SYSTEM_MB) * 2**20


def slice_drop_in(memory_mb: int) -> str:
    """The throwaway user's slice capped at `slice_memory`, with no swap."""
    return f"[Slice]\nMemoryMax={slice_memory(memory_mb)}\nMemorySwapMax=0\n"


def system_warning(os_release: str) -> str | None:
    """A warning when the machine is not the system the setup was run on."""
    values = dict(line.split("=", 1) for line in os_release.splitlines() if "=" in line)
    found = tuple(values.get(key, "").strip('"') for key in ("ID", "VERSION_ID"))
    if found == TESTED_ON:
        return None
    return (
        f"this is {' '.join(found).strip() or 'an unknown system'}, not Ubuntu "
        "24.04, the one this setup was run on: allowed when the self-test passes"
    )


# Running things.


def run(
    *command: str,
    check: bool = True,
    capture: bool = False,
    stdin: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        done = subprocess.run(
            command,
            check=False,
            text=True,
            input=stdin,
            capture_output=capture,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise MachineError(f"{command[0]} took over {timeout} s") from error
    if check and done.returncode != 0:
        detail = (done.stderr or "").strip() if capture else ""
        raise MachineError(
            f"{' '.join(command[:3])} exited {done.returncode}"
            + (f": {detail}" if detail else "")
        )
    return done


def as_user_done(
    user: str, uid: int, *command: str, **options: object
) -> subprocess.CompletedProcess[str]:
    environment = (
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    )
    return run(
        "runuser",
        "-u",
        user,
        "--",
        "env",
        *environment,
        *command,
        capture=True,
        **options,  # type: ignore[arg-type]
    )


def as_user(user: str, uid: int, *command: str, **options: object) -> str:
    return as_user_done(user, uid, *command, **options).stdout


def docker_done(
    slot: str, *arguments: str, check: bool = True, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    """The rootless daemon of the slot named `slot`, through its agent's
    socket, as root."""
    return run(
        "docker",
        "-H",
        f"unix://{daemon_socket(slot)}",
        *arguments,
        check=check,
        capture=True,
        timeout=timeout,
    )


def docker(slot: str, *arguments: str, check: bool = True, timeout: float = 300) -> str:
    return docker_done(slot, *arguments, check=check, timeout=timeout).stdout


def docker_as(
    grader: pwd.struct_passwd, slot: str, *arguments: str, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    """The rootless daemon of the slot named `slot`, through its agent's
    socket, as the throwaway user `grader`, whose daemon and socket they
    are: for what runs while gradings run, which root does not."""
    return as_user_done(
        grader.pw_name,
        grader.pw_uid,
        "docker",
        "-H",
        f"unix://{daemon_socket(slot)}",
        *arguments,
        check=False,
        timeout=timeout,
    )


def write(path: Path, text: str, mode: int = 0o644) -> None:
    """`text` at `path`, by way of a file made new, with its mode from the
    start, and renamed over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    with os.fdopen(descriptor, "w") as file:
        os.fchmod(file.fileno(), mode)
        file.write(text)
    temporary.replace(path)


def say(message: str) -> None:
    print(message, flush=True)


# Setup.


def setup(settings: Settings) -> int:
    if os.geteuid() != 0:
        raise MachineError("run as root")
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        raise MachineError(
            f"not installed: {', '.join(missing)}. Install docker-ce, "
            "docker-ce-rootless-extras, uidmap, nftables, util-linux, e2fsprogs "
            "and systemd"
        )
    secret = settings.agent_secret_file.read_text().strip()
    if not secret:
        raise MachineError(f"{settings.agent_secret_file} is empty")
    if architecture() not in settings.agent_binary_sha256:
        raise MachineError(
            f"agent_binary_sha256 gives no digest for this machine's {architecture()}"
        )
    memory_mb = memory_total() // 2**20
    slots = machine_slots(settings)
    disks = machine_disks(settings, slots)
    with contextlib.suppress(OSError):
        warning = system_warning(Path("/etc/os-release").read_text())
        if warning:
            say(f"warning: {warning}")

    # Nothing is graded until the self-test passes again, and a reboot on
    # the way does not start an agent. No step runs once the wall is down,
    # and the wall comes down through its own unit, written first so that
    # it knows how.
    disable_agents()
    grader = make_users(settings)
    uid = grader.pw_uid
    write(UNITS / "unicon-wall.service", wall_unit(uid))
    run("systemctl", "daemon-reload")
    stop_daemons(settings, uid)
    run("systemctl", "stop", "unicon-wall.service", check=False, capture=True)
    disable_root_docker()
    allow_rootlesskit()
    cache_size = make_disks(disks, grader)
    count_kills_locally(uid)

    write(STEP_HOSTS, step_hosts(settings.step_hosts))
    # Every daemon, as the throwaway user, reaches the hosts file to bind it.
    STEP_HOSTS.parent.chmod(0o755)
    write(CACHE_CAP, f"{cache_cap(settings, cache_size)}\n")
    # In place, not renamed over: in a container, as the stand-in is,
    # /etc/hosts is a mount, which a rename cannot replace.
    HOSTS.write_text(pinned_hosts(HOSTS.read_text(), settings.machine_hosts))
    write(
        SOCKET_TMPFILES,
        f"d {SOCKET_DIRECTORY} 0750 {settings.grader_user} {settings.grader_user} -\n",
    )
    run("systemd-tmpfiles", "--create", str(SOCKET_TMPFILES))
    write_daemons(settings, grader, slots)

    start_daemons(settings, grader, slots)
    for slot in slots:
        for image in (settings.test_image, *settings.pull):
            pull(slot.name, image)
    install_agent(settings, slots[0].name)

    write(WALL_FILE, wall_table(uid, settings.wall_allow))
    run("systemctl", "enable", "unicon-wall.service", capture=True)
    run("systemctl", "restart", "unicon-wall.service", capture=True)

    # One agent per slot. The environments of slots gone, and of the one
    # agent before slots, hold the token, so they go too.
    OLD_AGENT_ENV.unlink(missing_ok=True)
    (UNITS / OLD_AGENT).unlink(missing_ok=True)
    kept = {agent_environment_file(slot.name) for slot in slots}
    for stale in UNICON.glob("agent-*.env"):
        if stale not in kept:
            stale.unlink()
    agent = pwd.getpwnam(settings.agent_user)
    AGENT_STATE.mkdir(mode=0o700, exist_ok=True)
    os.chown(AGENT_STATE, agent.pw_uid, agent.pw_gid)
    for slot in slots:
        write(
            agent_environment_file(slot.name),
            agent_environment(settings, secret, slot),
            mode=0o600,
        )
        (AGENT_STATE / slot.name).mkdir(mode=0o700, exist_ok=True)
        os.chown(AGENT_STATE / slot.name, agent.pw_uid, agent.pw_gid)
    write(UNITS / AGENT_UNIT, agent_unit(settings, uid))
    write(
        UNITS / f"user-{uid}.slice.d" / "unicon-memory.conf", slice_drop_in(memory_mb)
    )
    # The low-disk check runs a copy of this script, so it does not depend
    # on where setup was run from.
    write(ROOM_SCRIPT, Path(__file__).read_text(), mode=0o755)
    write(UNITS / ROOM_UNIT, room_unit(settings.grader_user, slots))
    write(UNITS / ROOM_TIMER, room_timer())
    run("systemctl", "daemon-reload")

    if self_test(settings) != 0:
        say("the self-test failed, so no agent is enabled")
        return 1
    run("systemctl", "enable", "--now", *map(agent_name, slots), capture=True)
    run("systemctl", "enable", "--now", ROOM_TIMER, capture=True)
    for slot in slots:
        say(
            f"agent {slot.name} started: cores {slot.cpus}, "
            f"{slot.size.memory_mb} MB, labels {slot.labels(settings)}"
        )
    return 0


def agent_name(slot: Slot) -> str:
    """The unit of the slot's agent."""
    return f"unicon-agent@{slot.name}.service"


def disable_agents() -> None:
    """The low-disk check disabled and stopped first, since it starts an
    agent it stopped, and its marks cleared; then every agent disabled and
    stopped: the one before slots, each slot's enabled, one by one, as a
    unit missing fails the whole call, and any slot's still running."""
    run("systemctl", "disable", "--now", ROOM_TIMER, check=False, capture=True)
    run("systemctl", "stop", ROOM_UNIT, check=False, capture=True)
    shutil.rmtree(ROOM_STATE, ignore_errors=True)
    wants = UNITS / "multi-user.target.wants"
    enabled = sorted(path.name for path in wants.glob("unicon-agent@*.service"))
    for unit in (OLD_AGENT, *enabled):
        run("systemctl", "disable", "--now", unit, check=False, capture=True)
    run("systemctl", "stop", "unicon-agent@*.service", check=False, capture=True)


def machine_cpus() -> list[int]:
    """The machine's online CPUs, as cpuset numbers them."""
    return read_cpu_list(ONLINE_CPUS.read_text())


def machine_slots(settings: Settings) -> tuple[Slot, ...]:
    """This machine's slots, from `SLOT_TABLE`, its CPUs, its memory and
    its disk."""
    try:
        text = SLOT_TABLE.read_text()
    except OSError as error:
        raise MachineError(
            f"cannot read {SLOT_TABLE}, the slot sizes, kept beside this script: "
            f"{error}"
        ) from error
    return plan_slots(
        read_sizes(text),
        settings,
        machine_cpus(),
        memory_total() // 2**20,
        disks_room(),
    )


def machine_disks(settings: Settings, slots: Sequence[Slot]) -> tuple[Disk, ...]:
    """Every disk setup makes: each slot's, then the cache's."""
    cache = Disk(CACHE_DISK.with_suffix(".img"), settings.cache_gb * 2**30)
    return (*(slot.disk for slot in slots), cache)


def allocated(path: Path) -> int:
    """The bytes the file at `path` holds on the machine's disk."""
    return path.stat().st_blocks * 512


def disks_room() -> int:
    """The bytes the machine's disk under `DISKS` has for the disks setup
    makes: what is free, and what every disk made before holds, a disk's
    file left half made among them, since `make_disks` gives back each it
    removes or makes again before it makes any. So setup run again finds
    the room it found before."""
    below = DISKS
    while not below.exists():
        below = below.parent
    made = [
        image
        for folder in (DISKS, SLOT_DISKS)
        for pattern in ("*.img", "*.img.new")
        for image in folder.glob(pattern)
        if image.is_file()
    ]
    return shutil.disk_usage(below).free + sum(map(allocated, made))


def make_users(settings: Settings) -> pwd.struct_passwd:
    for user in (settings.grader_user, settings.agent_user):
        try:
            pwd.getpwnam(user)
        except KeyError:
            say(f"making the user {user}")
            if user == settings.grader_user:
                run(
                    "useradd",
                    "--create-home",
                    "--home-dir",
                    f"/home/{user}",
                    "--shell",
                    "/usr/sbin/nologin",
                    user,
                )
            else:
                run(
                    "useradd",
                    "--system",
                    "--home-dir",
                    str(AGENT_STATE),
                    "--shell",
                    "/usr/sbin/nologin",
                    user,
                )
    run("usermod", "--append", "--groups", settings.grader_user, settings.agent_user)
    grader = pwd.getpwnam(settings.grader_user)
    for table in ("/etc/subuid", "/etc/subgid"):
        if not subordinate_count(Path(table), settings.grader_user) >= 65536:
            raise MachineError(
                f"{table} gives {settings.grader_user} fewer than 65,536 ids"
            )
    write(
        UNITS / "user@.service.d/delegate.conf",
        "[Service]\nDelegate=" + " ".join(DELEGATED) + "\n",
    )
    run("systemctl", "daemon-reload")
    run("loginctl", "enable-linger", settings.grader_user)
    runtime = Path(f"/run/user/{grader.pw_uid}")
    for _ in range(30):
        if (runtime / "bus").exists():
            break
        time.sleep(1)
    else:
        raise MachineError(f"{settings.grader_user}'s systemd did not start")
    return grader


def stop_daemons(settings: Settings, uid: int) -> None:
    """Kill every container of every daemon of the throwaway user's, each
    found by its socket, then stop the daemons, before the wall comes
    down: a step's container outlives its agent. The daemons start again
    only once the settings are in place. Refuses to go on, the wall still
    up, while a daemon answers."""
    found = sorted(path.stem for path in SOCKET_DIRECTORY.glob("*.sock"))
    for slot in found:
        running = docker(slot, "ps", "--quiet", check=False, timeout=60).split()
        if running:
            docker(slot, "kill", *running, check=False, timeout=60)
    as_user(
        settings.grader_user,
        uid,
        "systemctl",
        "--user",
        "stop",
        "unicon-docker@*.service",
        check=False,
        timeout=120,
    )
    # With no daemon to answer, the CLI still prints an empty line.
    answering = [
        slot
        for slot in found
        if docker(
            slot, "info", "--format", "{{.ServerVersion}}", check=False, timeout=60
        ).strip()
    ]
    if answering:
        raise MachineError(
            f"the daemon of {', '.join(answering)} did not stop, so the wall stays up"
        )


def subordinate_count(table: Path, user: str) -> int:
    total = 0
    for line in table.read_text().splitlines():
        name, _, rest = line.partition(":")
        if name == user:
            total += int(rest.split(":")[1])
    return total


def disable_root_docker() -> None:
    """The root daemon `docker-ce` starts, stopped and kept from starting:
    no step uses it, and its group is root on the machine."""
    found = run(
        "systemctl", "disable", "--now", *ROOT_DOCKER, check=False, capture=True
    )
    if found.returncode == 0:
        say("root Docker: stopped and disabled")


def allow_rootlesskit() -> None:
    if not APPARMOR_SWITCH.exists() or APPARMOR_SWITCH.read_text().strip() != "1":
        say("AppArmor does not restrict user namespaces here: no profile needed")
        return
    write(APPARMOR_PROFILE, rootlesskit_profile())
    run("apparmor_parser", "--replace", str(APPARMOR_PROFILE))
    say("AppArmor: rootlesskit may make its user namespace")


def make_disks(disks: Sequence[Disk], grader: pwd.struct_passwd) -> int:
    """Each disk made once, kept on every run after, and mounted at every
    boot (`fstab`); the cache's filesystem's size in bytes.

    A disk is made as a file reserved whole, then formatted with no blocks
    kept for root, which no step is, and no discard, which through the
    loop device would give the reserved blocks back. It is made under
    another name and renamed, so a file of its name is always a disk. One
    made before is reserved again, which fills any hole and keeps its
    data; one of another size is made again, empty, and a slot's gone is
    removed. Each disk's root belongs to the throwaway user: a slot's
    is its daemon's data root, and on the cache's Docker makes an org's
    folder (0755) the first time a step of that org mounts it, since orgs
    appear after setup. Run with every daemon stopped.
    """
    # The daemons, as the throwaway user, pass through to their data roots.
    SLOT_DISKS.mkdir(parents=True, exist_ok=True)
    for folder in (DISKS, SLOT_DISKS):
        folder.chmod(0o755)
    # Every disk not kept is given back before any is made, as the room
    # for them was counted (`disks_room`): one left half made by a run
    # that stopped, a slot's gone, and one of another size.
    for folder in (DISKS, SLOT_DISKS):
        for made in folder.glob("*.img.new"):
            made.unlink()
    wanted = {disk.image for disk in disks}
    for image in sorted(SLOT_DISKS.glob("*.img")):
        if image not in wanted:
            say(f"removing the disk of the slot {image.stem}, gone")
            unmount(image.with_suffix(""))
            image.unlink()
            with contextlib.suppress(FileNotFoundError):
                image.with_suffix("").rmdir()
    for disk in disks:
        if disk.image.is_file() and disk.image.stat().st_size != disk.size:
            say(f"disk {disk.mount}: made again at {disk.size // 2**30} GiB, empty")
            unmount(disk.mount)
            disk.image.unlink()
    for disk in disks:
        if disk.image.is_file():
            run("fallocate", "--length", str(disk.size), str(disk.image))
        else:
            made = disk.image.with_name(disk.image.name + ".new")
            os.close(os.open(made, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            run("fallocate", "--length", str(disk.size), str(made))
            run("mkfs.ext4", "-q", "-F", "-m", "0", "-E", "nodiscard", str(made))
            made.replace(disk.image)
        disk.mount.mkdir(mode=0o755, exist_ok=True)
    write(FSTAB, fstab(FSTAB.read_text(), disks, grader.pw_uid))
    run("systemctl", "daemon-reload")
    for disk in disks:
        if not os.path.ismount(disk.mount):
            run("mount", str(disk.mount), capture=True)
        if not os.path.ismount(disk.mount):
            raise MachineError(f"{disk.image} did not mount at {disk.mount}")
        os.chown(disk.mount, grader.pw_uid, grader.pw_gid)
        disk.mount.chmod(0o700)
        usage = shutil.disk_usage(disk.mount)
        say(f"disk {disk.mount}: {usage.total / 2**30:.1f} GiB of its own")
    return shutil.disk_usage(CACHE_DISK).total


def unmount(mount: Path) -> None:
    if os.path.ismount(mount):
        run("umount", str(mount), capture=True)


def count_kills_locally(uid: int) -> None:
    options = cgroup_options(MOUNTINFO.read_text())
    write(CGROUP_UNIT, cgroup_unit(uid, options))
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "unicon-cgroup.service", capture=True)
    started = run(
        "systemctl", "restart", "unicon-cgroup.service", check=False, capture=True
    )
    if started.returncode != 0:
        say(f"warning: {CGROUP} did not take {LOCAL_EVENTS}; the self-test says why")


def write_daemons(
    settings: Settings, grader: pwd.struct_passwd, slots: Sequence[Slot]
) -> None:
    """Each slot's daemon.json and the daemons' unit, as the throwaway user,
    whose folder it is: root writing there would follow whatever links
    that user left in it. The daemon.json of each slot gone is removed,
    and its daemon disabled."""
    user, uid, home = settings.grader_user, grader.pw_uid, Path(grader.pw_dir)
    configs = home / ".config/docker"
    files = [
        (configs, f"daemon-{slot.name}.json", daemon_json(settings, uid, slot.name))
        for slot in slots
    ]
    files.append((home / ".config/systemd/user", DAEMON_UNIT, daemon_unit()))
    for folder, name, text in files:
        as_user(
            user,
            uid,
            "sh",
            "-c",
            'umask 077 && mkdir -p "$1" && cat > "$1/$2.new" '
            '&& mv -f "$1/$2.new" "$1/$2"',
            "sh",
            str(folder),
            name,
            stdin=text,
        )
    names = {slot.name for slot in slots}
    as_user(user, uid, "systemctl", "--user", "daemon-reload")
    for path in sorted(configs.glob("daemon-*.json")):
        slot = path.name.removeprefix("daemon-").removesuffix(".json")
        if slot not in names:
            unit = f"unicon-docker@{slot}.service"
            as_user(user, uid, "systemctl", "--user", "disable", unit)
            as_user(user, uid, "rm", "-f", str(path))


def start_daemons(
    settings: Settings, grader: pwd.struct_passwd, slots: Sequence[Slot]
) -> None:
    """Every slot's daemon enabled and started again, and each waited for:
    rootlesskit copies /etc as a daemon starts, so the names pinned above
    reach it only through a restart."""
    user, uid = settings.grader_user, grader.pw_uid
    as_user(user, uid, "dockerd-rootless-setuptool.sh", "check", timeout=300)
    units = [f"unicon-docker@{slot.name}.service" for slot in slots]
    as_user(user, uid, "systemctl", "--user", "enable", *units)
    as_user(user, uid, "systemctl", "--user", "reset-failed", *units)
    as_user(user, uid, "systemctl", "--user", "restart", *units, timeout=300)
    for slot in slots:
        for _ in range(60):
            if docker(
                slot.name, "info", "--format", "{{.ServerVersion}}", check=False
            ).strip():
                break
            time.sleep(1)
        else:
            raise MachineError(
                f"the daemon of slot {slot.name} did not answer on "
                f"{daemon_socket(slot.name)}"
            )


def pull(slot: str, image: str, tries: int = 3) -> None:
    """A pull into the slot's daemon, tried again a few seconds later, since
    a registry that drops one request, or a daemon that has just started,
    should not stop an enrolment.
    """
    say(f"pulling {image} into slot {slot}")
    for attempt in range(tries):
        try:
            docker(slot, "pull", "-q", image, timeout=900)
            return
        except MachineError:
            if attempt == tries - 1:
                raise
            time.sleep(5)


def install_agent(settings: Settings, slot: str) -> None:
    """The agent binary, copied out of its pinned image through the slot's
    daemon, so the agent runs on the machine as its own user rather than
    in a container of the throwaway user's daemon, whose traffic the wall
    would hold. The daemon is the throwaway user's, so what it hands over
    is installed only as a plain file with the SHA-256 the settings pin
    for this architecture.
    """
    expected = settings.agent_binary_sha256[architecture()]
    pull(slot, settings.agent_image)
    container = docker(slot, "create", settings.agent_image).strip()
    staging = Path(
        tempfile.mkdtemp(prefix=".woodpecker-agent-", dir=AGENT_BINARY.parent)
    )
    try:
        copied = staging / AGENT_BINARY.name
        docker(slot, "cp", f"{container}:/bin/woodpecker-agent", str(copied))
        if copied.is_symlink() or not copied.is_file():
            raise MachineError("the agent image's /bin/woodpecker-agent is not a file")
        digest = hashlib.sha256(copied.read_bytes()).hexdigest()
        if digest != expected:
            raise MachineError(
                f"the agent binary's SHA-256 is {digest}, not the pinned {expected}"
            )
        os.chown(copied, 0, 0)
        copied.chmod(0o755)
        copied.replace(AGENT_BINARY)
    finally:
        docker(slot, "rm", container, check=False)
        shutil.rmtree(staging, ignore_errors=True)


def memory_total() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise MachineError("cannot read the machine's memory")


# The self-test.

PROBE = """
import errno, json, socket, sys
def said(error):
    return errno.errorcode.get(error.errno, type(error).__name__)
out = {}
for address, port in json.loads(sys.argv[1]):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
    except OSError as error:
        out[f"{address}:{port}"] = said(error)
        continue
    s.settimeout(4)
    try:
        s.connect((address, port))
        out[f"{address}:{port}"] = "connected"
    except OSError as error:
        out[f"{address}:{port}"] = said(error)
    finally:
        s.close()
try:
    socket.getaddrinfo("example.com", 443)
    out["lookup"] = "answered"
except OSError as error:
    out["lookup"] = type(error).__name__
print(json.dumps(out))
"""

MEMORY_HOG = "x = bytearray(256 * 2**20); print('kept', len(x))"
FORK_BOMB = """
import os, time
made = 0
try:
    for _ in range(200):
        if os.fork() == 0:
            time.sleep(5); os._exit(0)
        made += 1
except OSError:
    pass
print(made)
"""
# What a box is to the step container exec runs in: a child cgroup with a
# memory limit of its own, whose process is killed for going over it.
BOX_KILLED = """
import os, subprocess
root = "/sys/fs/cgroup"
os.makedirs(root + "/init", exist_ok=True)
with open(root + "/init/cgroup.procs", "w") as f:
    f.write(str(os.getpid()))
with open(root + "/cgroup.subtree_control", "w") as f:
    f.write("+memory")
os.makedirs(root + "/box", exist_ok=True)
with open(root + "/box/memory.max", "w") as f:
    f.write(str(32 * 2**20))
try:
    with open(root + "/box/memory.swap.max", "w") as f:
        f.write("0")
except OSError:
    pass
def enter():
    with open(root + "/box/cgroup.procs", "w") as f:
        f.write("0")
subprocess.call(["python3", "-c", "x = bytearray(256 * 2**20)"], preexec_fn=enter)
events = dict(line.split() for line in open(root + "/box/memory.events"))
print(events.get("oom_kill", "0"))
"""
BLOCK_DEVICES = """
import os, stat
opened = []
for name in sorted(os.listdir("/dev")):
    path = "/dev/" + name
    try:
        if not stat.S_ISBLK(os.lstat(path).st_mode):
            continue
        os.close(os.open(path, os.O_RDONLY))
        opened.append(name)
    except OSError:
        pass
print(" ".join(opened))
"""


@dataclass
class Report:
    failed: list[str] = field(default_factory=list)

    def check(self, passed: bool, what: str, detail: str = "") -> bool:
        say(f"{'ok  ' if passed else 'FAIL'} {what}{f' ({detail})' if detail else ''}")
        if not passed:
            self.failed.append(what)
        return passed

    def untested(self, what: str, why: str) -> None:
        say(f"--   {what} (not tested: {why})")


def self_test(settings: Settings) -> int:
    report = Report()
    grader = pwd.getpwnam(settings.grader_user)
    slots = machine_slots(settings)
    say("self-test")
    wall_up = (
        run(
            "nft", "list", "table", "inet", WALL_TABLE, check=False, capture=True
        ).returncode
        == 0
    )
    report.check(wall_up, f"the nftables table {WALL_TABLE} is loaded")
    if not check_daemons(grader, slots, report):
        say(f"{len(report.failed)} checks failed, and the rest need every daemon")
        return 1
    check_root_docker(report)
    first = slots[0].name
    held = check_reach(settings, grader, slots, first, report)
    check_limits(settings, first, report)
    check_slots(settings, grader, slots, report)
    check_disks(settings, slots, report)
    check_cache(settings, grader, slots, report)
    check_kills(settings, first, report)
    check_block_devices(settings, first, report)
    check_kernel(report)
    say(f"network wall: {'on' if wall_up and held else 'off'}")
    if report.failed:
        say(f"{len(report.failed)} checks failed")
        return 1
    return 0


def check_daemons(
    grader: pwd.struct_passwd, slots: Sequence[Slot], report: Report
) -> bool:
    """Each slot's daemon answers, rootless on cgroup v2 under systemd, with
    its data root on its slot's disk; and the controllers are delegated to
    the throwaway user, whose daemons they all are. False when a daemon
    does not answer."""
    answered = True
    for slot in slots:
        done = docker_done(slot.name, "info", "--format", "{{json .}}", check=False)
        if not report.check(
            done.returncode == 0,
            f"slot {slot.name}'s daemon answers on {daemon_socket(slot.name)}",
            f"exit {done.returncode}" if done.returncode else "",
        ):
            answered = False
            continue
        info = json.loads(done.stdout)
        options = " ".join(info.get("SecurityOptions") or [])
        report.check(
            "name=rootless" in options,
            f"slot {slot.name}'s daemon is rootless",
            options,
        )
        report.check(
            info.get("CgroupVersion") == "2" and info.get("CgroupDriver") == "systemd",
            f"slot {slot.name}'s daemon runs on cgroup v2 under systemd",
            f"{info.get('CgroupDriver')} v{info.get('CgroupVersion')}",
        )
        root = Path(str(info.get("DockerRootDir")))
        report.check(
            root == slot.disk.mount and os.path.ismount(root),
            f"slot {slot.name}'s data root is its own disk",
            str(root),
        )
    uid = grader.pw_uid
    controllers = Path(
        f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/"
        "cgroup.controllers"
    )
    have = set(controllers.read_text().split()) if controllers.exists() else set()
    report.check(
        set(DELEGATED) <= have,
        "cpu, cpuset, io, memory and pids are delegated",
        " ".join(sorted(have)),
    )
    return answered


def check_root_docker(report: Report) -> None:
    states = run(
        "systemctl", "is-active", *ROOT_DOCKER, check=False, capture=True
    ).stdout.split()
    active = [
        unit
        for unit, state in zip(ROOT_DOCKER, states, strict=False)
        if state == "active"
    ]
    report.check(
        not active,
        "the root Docker is stopped",
        f"{' and '.join(active)} active" if active else "",
    )


def machine_addresses() -> list[str]:
    found = json.loads(run("ip", "-j", "address", capture=True).stdout)
    addresses = []
    for link in found:
        for entry in link.get("addr_info", []):
            if entry.get("scope") == "global":
                addresses.append(entry["local"])
    return addresses


@contextlib.contextmanager
def listening() -> Iterator[int]:
    """A listener on every address of the machine, IPv4 and IPv6, on a free
    port, so a refused connection is the wall's and not an empty port's.
    """
    server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("::", 0))
    server.listen(64)
    server.settimeout(0.5)
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            with contextlib.suppress(OSError):
                server.accept()[0].close()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1]
    finally:
        stop.set()
        thread.join()
        server.close()


def probe(targets: Sequence[Target], *before: str) -> dict[str, str]:
    """What `PROBE` got for each target and for a lookup, as root on the
    machine, or run after `before`."""
    found = json.dumps([[t.address, t.port] for t in targets])
    output = run(*before, "python3", "-c", PROBE, found, capture=True, timeout=120)
    return dict(json.loads(output.stdout.strip().splitlines()[-1]))


def held_at(report: Report, what: str, control: str, seen: str, side: str) -> bool:
    """One target the wall must hold, from what root got (`control`), which
    the wall does not hold, and what `side` got (`seen`). False only when
    it got through; "not tested" when root does not get there either, or
    `side` has no route, since then no wall refused it.
    """
    if control not in ("connected", "answered"):
        report.untested(what, f"the machine itself does not get there: {control}")
        return True
    if seen in NO_ROUTE:
        report.untested(what, f"{side} has no route there: {seen}")
        return True
    return report.check(seen not in ("connected", "answered"), what, seen)


def check_reach(
    settings: Settings,
    grader: pwd.struct_passwd,
    slots: Sequence[Slot],
    daemon: str,
    report: Report,
) -> bool:
    """From a container of the slot `daemon`'s daemon on a network made as
    Woodpecker makes one per pipeline, then as the throwaway user in the
    machine's own network namespace, where the daemons' pulls leave, each
    target tried from root first. True when nothing the wall should hold
    got through.
    """
    network = "unicon-self-test"
    docker(daemon, "network", "rm", network, check=False)
    docker(daemon, "network", "create", "--ipv6", network, check=False)
    if network not in docker(daemon, "network", "ls", "--format", "{{.Name}}"):
        docker(daemon, "network", "create", network)
    allowed = settings.wall_allow[0]
    try:
        with listening() as port:
            # The listener answers on every address of the machine, so
            # root's own reach of these needs no trying.
            own = [Target(address, port) for address in machine_addresses()]
            own.append(Target("10.0.2.2", port))
            loopback = [
                Target("127.0.0.53", 53),
                *(Target("127.0.0.1", slot.port) for slot in slots),
                Target("127.0.0.1", port),
            ]
            control = probe([*settings.outside, *loopback])
            control |= {f"{t.address}:{t.port}": "connected" for t in own}
            targets = [allowed, *settings.outside, *own]
            raw = docker(
                daemon,
                "run",
                "--rm",
                "--network",
                network,
                "-v",
                f"{STEP_HOSTS}:/etc/hosts:ro",
                "--entrypoint",
                "python3",
                settings.test_image,
                "-c",
                PROBE,
                json.dumps([[t.address, t.port] for t in targets]),
                timeout=120,
            )
            step = dict(json.loads(raw.strip().splitlines()[-1]))
            user = probe(
                [*settings.outside, *loopback],
                "runuser",
                "-u",
                settings.grader_user,
                "--",
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{grader.pw_uid}",
                "PATH=/usr/local/bin:/usr/bin:/bin",
            )
    finally:
        docker(daemon, "network", "rm", network, check=False)

    def key(target: Target) -> str:
        return f"{target.address}:{target.port}"

    report.check(
        step[key(allowed)] == "connected",
        f"a step reaches {allowed}, the first address the wall allows",
        step[key(allowed)],
    )
    held = True
    for target in settings.outside:
        what = f"a step does not reach {target} outside"
        held &= held_at(report, what, control[key(target)], step[key(target)], "a step")
    tested = [
        target
        for target in settings.outside
        if control[key(target)] == "connected" and step[key(target)] not in NO_ROUTE
    ]
    report.check(
        bool(tested),
        "at least one outside address was tested from a step",
        "" if tested else "name one in `outside` that the machine reaches",
    )
    for target in own:
        what = f"a step does not reach the machine at {target}"
        held &= held_at(report, what, control[key(target)], step[key(target)], "a step")
    held &= held_at(
        report,
        "a step's lookup gets no answer",
        control["lookup"],
        step["lookup"],
        "a step",
    )
    for target in [*settings.outside, *loopback]:
        what = f"the throwaway user does not reach {target}"
        side = "the throwaway user"
        held &= held_at(report, what, control[key(target)], user[key(target)], side)
    held &= held_at(
        report,
        "the throwaway user's lookup gets no answer",
        control["lookup"],
        user["lookup"],
        "the throwaway user",
    )
    return held


SLOT_LIMITS = """
from pathlib import Path
cgroup = Path("/sys/fs/cgroup")
print(*(
    (cgroup / name).read_text().strip()
    for name in ("cpuset.cpus.effective", "memory.max", "memory.swap.max")
))
"""


def check_slots(
    settings: Settings,
    grader: pwd.struct_passwd,
    slots: Sequence[Slot],
    report: Report,
) -> None:
    """Each slot's limits, as its agent sets them on every container, read
    from inside a container of its daemon made with them: its cores alone,
    its memory and no swap. And the throwaway user's slice capped as
    `slice_drop_in` says, over every container of every slot."""
    for slot in slots:
        seen = docker(
            slot.name,
            "run",
            "--rm",
            "--cpuset-cpus",
            slot.cpus,
            "--memory",
            str(slot.memory),
            "--memory-swap",
            str(slot.memory),
            "--entrypoint",
            "python3",
            settings.test_image,
            "-c",
            SLOT_LIMITS,
            check=False,
            timeout=120,
        ).strip()
        report.check(
            seen == f"{slot.cpus} {slot.memory} 0",
            f"slot {slot.name}'s containers have cores {slot.cpus}, "
            f"{slot.size.memory_mb} MB and no swap",
            seen or "nothing",
        )
    uid = grader.pw_uid
    held = CGROUP / "user.slice" / f"user-{uid}.slice" / "memory.max"
    wanted = str(slice_memory(memory_total() // 2**20))
    found = held.read_text().strip() if held.exists() else "nothing"
    report.check(
        found == wanted,
        f"the throwaway user's slice is capped at {wanted} bytes",
        found,
    )


# A write past the container's own disk: its layer is filled, reserved a
# gigabyte at a time and then less, down to a block, so that no data is
# written, and then one more write is made. The fill is removed before the
# container ends.
FILL_DISK = """
import errno, json, os
path = "/tmp/unicon-fill"
fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
filled, chunk, write = 0, 2**30, "written"
try:
    while chunk >= 4096:
        try:
            os.posix_fallocate(fd, filled, chunk)
            filled += chunk
        except OSError as error:
            if error.errno != errno.ENOSPC:
                raise
            chunk //= 2
    try:
        for n in range(64):
            os.pwrite(fd, b"x" * 2**16, filled + n * 2**16)
    except OSError as error:
        write = errno.errorcode.get(error.errno, str(error.errno))
finally:
    os.close(fd)
    os.unlink(path)
print(json.dumps({"filled": filled, "write": write}))
"""
CACHE_WRITE = "open('/cache/token', 'w').write({token!r})"
CACHE_READ = """
import os
print(open("/cache/token").read(), *(os.stat("/cache/token")[4:6]))
"""


def check_disks(settings: Settings, slots: Sequence[Slot], report: Report) -> None:
    """Every disk fully allocated on the machine's disk, so the machine's
    disk cannot fill first; and for each slot, a container of its daemon
    writing past its disk gets `ENOSPC`, and the other slots' disks stay as
    they were. Not tested while an agent runs, as when the self-test is
    run by hand on a machine that grades: a full disk would fail the
    gradings it takes."""
    for disk in machine_disks(settings, slots):
        held = allocated(disk.image) if disk.image.is_file() else 0
        report.check(
            held >= disk.size,
            f"{disk.image} is fully allocated on the machine's disk",
            f"{held // 2**20} of {disk.size // 2**20} MiB",
        )
    listed = run(
        "systemctl",
        "list-units",
        "--state=active",
        "--plain",
        "--no-legend",
        "unicon-agent@*.service",
        check=False,
        capture=True,
    )
    taking = [line.split()[0] for line in listed.stdout.splitlines() if line.strip()]
    if taking:
        report.untested(
            "a container of each slot writing past its disk gets ENOSPC",
            f"an agent takes gradings, {', '.join(taking)}; stop the agents first",
        )
        return
    for slot in slots:
        others = [other for other in slots if other != slot]
        before = [shutil.disk_usage(other.disk.mount).used for other in others]
        raw = docker(
            slot.name,
            "run",
            "--rm",
            "--entrypoint",
            "python3",
            settings.test_image,
            "-c",
            FILL_DISK,
            check=False,
            timeout=600,
        )
        after = [shutil.disk_usage(other.disk.mount).used for other in others]
        try:
            found = dict(json.loads(raw.strip().splitlines()[-1]))
        except (IndexError, ValueError):
            found = {"filled": 0, "write": raw.strip() or "nothing"}
        report.check(
            found["write"] == "ENOSPC",
            f"a container of slot {slot.name} writing past its disk gets ENOSPC",
            f"{found['filled'] / 2**30:.1f} GiB filled, then {found['write']}",
        )
        grown = [later - earlier for earlier, later in zip(before, after, strict=True)]
        if others:
            report.check(
                all(abs(change) < 2**20 for change in grown),
                f"the other slots' disks stay as they were while slot {slot.name}'s "
                "is full",
                ", ".join(
                    f"{other.name} {change:+d} bytes"
                    for other, change in zip(others, grown, strict=True)
                ),
            )


def check_cache(
    settings: Settings,
    grader: pwd.struct_passwd,
    slots: Sequence[Slot],
    report: Report,
) -> None:
    """The cache's disk is a filesystem of its own, the throwaway user's; a
    folder of it is made on first use by the first slot's daemon, 0755 and
    the throwaway user's, as an org's is when the forge first mounts it at
    a step's `/cache`; and every other slot reads what the first wrote
    there, with the same owner."""
    owner = CACHE_DISK.stat().st_uid if CACHE_DISK.exists() else None
    report.check(
        os.path.ismount(CACHE_DISK) and owner == grader.pw_uid,
        f"the cache's disk {CACHE_DISK} is its own, the throwaway user's",
        f"owner {owner}",
    )
    shutil.rmtree(CACHE_FOLDER_TEST, ignore_errors=True)
    token = os.urandom(8).hex()
    mount = ("-v", f"{CACHE_FOLDER_TEST}:/cache", "--entrypoint", "python3")
    first, *others = slots
    try:
        wrote = docker_done(
            first.name,
            "run",
            "--rm",
            *mount,
            settings.test_image,
            "-c",
            CACHE_WRITE.format(token=token),
            check=False,
            timeout=120,
        )
        made = CACHE_FOLDER_TEST.stat() if CACHE_FOLDER_TEST.is_dir() else None
        report.check(
            wrote.returncode == 0
            and made is not None
            and made.st_mode & 0o777 == 0o755
            and made.st_uid == grader.pw_uid,
            "a folder of the cache's disk is made on first use, 0755, the "
            "throwaway user's",
            f"exit {wrote.returncode}"
            + (f", mode {made.st_mode & 0o777:o}, owner {made.st_uid}" if made else ""),
        )
        for other in others:
            seen = docker(
                other.name,
                "run",
                "--rm",
                *mount,
                settings.test_image,
                "-c",
                CACHE_READ,
                check=False,
                timeout=120,
            ).strip()
            report.check(
                seen == f"{token} 0 0",
                f"slot {other.name} reads what slot {first.name} wrote to /cache, "
                "as the same owner",
                seen or "nothing",
            )
        if not others:
            report.untested(
                "a write to /cache from one slot is read from another",
                "this machine has one slot",
            )
    finally:
        shutil.rmtree(CACHE_FOLDER_TEST, ignore_errors=True)


def check_limits(settings: Settings, daemon: str, report: Report) -> None:
    limited = ("--memory", "64m", "--memory-swap", "64m", "--pids-limit", "32")
    hog = docker_done(
        daemon,
        "run",
        "--rm",
        *limited,
        "--entrypoint",
        "python3",
        settings.test_image,
        "-c",
        MEMORY_HOG,
        check=False,
        timeout=120,
    )
    report.check(
        hog.returncode == 137,
        "a container over its memory limit is killed",
        f"exit {hog.returncode}",
    )
    forks = docker(
        daemon,
        "run",
        "--rm",
        *limited,
        "--entrypoint",
        "python3",
        settings.test_image,
        "-c",
        FORK_BOMB,
        check=False,
        timeout=120,
    ).strip()
    report.check(
        forks.isdigit() and int(forks) < 32,
        "a container stops at its process limit",
        f"{forks or 'nothing'} processes made of 200",
    )


def check_kills(settings: Settings, daemon: str, report: Report) -> None:
    """A container's own out-of-memory kill marks it killed, which the agent
    fails a step for; its child cgroup's, as a box's is, does not, when
    the hierarchy counts each cgroup's kills alone."""
    mounted = cgroup_mount(MOUNTINFO.read_text())
    report.check(
        LOCAL_EVENTS in mounted,
        f"{CGROUP} counts each cgroup's memory kills alone ({LOCAL_EVENTS})",
        ",".join(mounted),
    )
    remount = remounted_with(CGROUP_UNIT.read_text()) if CGROUP_UNIT.is_file() else []
    lost = [option for option in remount if option not in mounted]
    report.check(
        bool(remount) and not lost,
        f"{CGROUP} kept every option it was remounted with",
        f"lost {','.join(lost)}" if lost else "" if remount else f"no {CGROUP_UNIT}",
    )
    for name, arguments, wanted, what in (
        (
            "unicon-self-test-own",
            (
                "--memory",
                "64m",
                "--memory-swap",
                "64m",
                "--entrypoint",
                "python3",
                settings.test_image,
                "-c",
                MEMORY_HOG,
            ),
            "true",
            "a container over its own memory is marked killed",
        ),
        (
            "unicon-self-test-box",
            (
                "--privileged",
                "--entrypoint",
                "python3",
                settings.test_image,
                "-c",
                BOX_KILLED,
            ),
            "false",
            "a container whose box was killed for its memory is not marked killed",
        ),
    ):
        docker(daemon, "rm", "-f", name, check=False)
        try:
            ran = docker_done(
                daemon, "run", "--name", name, *arguments, check=False, timeout=120
            )
            marked = docker(
                daemon, "inspect", "--format", "{{.State.OOMKilled}}", name
            ).strip()
        finally:
            docker(daemon, "rm", "-f", name, check=False)
        detail = f"exit {ran.returncode}, OOMKilled {marked}"
        if name.endswith("box"):
            killed = ran.stdout.strip().splitlines()[-1:] == ["1"]
            report.check(
                marked == wanted and ran.returncode == 0 and killed,
                what,
                detail + ("" if killed else ", the box was not killed"),
            )
        else:
            report.check(marked == wanted, what, detail)


def check_block_devices(settings: Settings, daemon: str, report: Report) -> None:
    opened = docker(
        daemon,
        "run",
        "--rm",
        "--privileged",
        "--entrypoint",
        "python3",
        settings.test_image,
        "-c",
        BLOCK_DEVICES,
        timeout=120,
    ).strip()
    report.check(
        not opened,
        "a privileged container opens no block device",
        f"opened {opened}" if opened else "",
    )


def check_kernel(report: Report) -> None:
    for name, least, what in KERNEL:
        value = read_sysctl(name)
        report.check(
            value is not None and value >= least,
            f"{name} at least {least}: {what}",
            f"is {value}",
        )
    value = read_sysctl(USERFAULTFD)
    report.check(
        value in (None, 0),
        f"{USERFAULTFD} 0 or absent: userfaultfd for user-mode faults only",
        "absent" if value is None else f"is {value}",
    )


def read_sysctl(name: str) -> int | None:
    path = Path("/proc/sys") / name.replace(".", "/")
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError):
        return None


# The low-disk check.


def check_room(grader: pwd.struct_passwd, slot: str) -> None:
    """A nearly full slot (`room_mark`) stops taking gradings until its disk
    has room again, from a timer every minute. Stopping an agent kills its
    running workflow and leaves that workflow's container, network and
    volume behind, so the agent is stopped only once its slot's daemon has
    no workflow container and no workflow network (`workflow_running`): a
    workflow between its steps, or pulling its next step's image, runs no
    container, but its network lives from its start to its end. A
    network older than any workflow runs is one an agent stopped
    mid-workflow left behind, and is removed with the rest. One taken
    between that look and the stop is killed, which the forge reads as
    lost. Then the daemon's stopped containers, unused volumes, networks
    and dangling images are removed, never the pinned images, which the
    wall would keep it from pulling again, and the agent started again
    once the disk is back above the mark. The pool's other slots take the
    gradings meanwhile. Root only stops and starts the agent; the daemon
    is driven as the throwaway user `grader` (`docker_as`), whose it is.
    A slot whose agent is not enabled, as while setup runs or after its
    self-test failed, or whose disk is not mounted, is left alone.
    """
    mount = SLOT_DISKS / slot
    agent = f"unicon-agent@{slot}.service"
    stopped = ROOM_STATE / f"{slot}.stopped"
    if not os.path.ismount(mount):
        say(f"{slot}: its disk is not mounted, so it is left alone")
        return
    enabled = run("systemctl", "is-enabled", agent, check=False, capture=True)
    if enabled.stdout.strip() != "enabled":
        say(f"{slot}: its agent is not enabled, so it is left alone")
        return

    def room() -> tuple[int, int]:
        usage = shutil.disk_usage(mount)
        return usage.free, room_mark(usage.total)

    free, mark = room()
    if free >= mark:
        if stopped.exists():
            run("systemctl", "start", agent, capture=True)
            stopped.unlink()
            say(f"{slot}: {free / 2**30:.2f} GiB free again, so its agent started")
        return
    below = f"{free / 2**30:.2f} GiB free, below {mark / 2**30:.2f} GiB"
    containers = docker_as(
        grader,
        slot,
        "container",
        "ls",
        "--filter",
        "name=^wp_",
        "--format",
        "{{.Names}}",
    )
    networks = docker_as(
        grader, slot, "network", "ls", "--quiet", "--filter", "name=^wp_"
    )
    inspected = (
        docker_as(grader, slot, "network", "inspect", *networks.stdout.split())
        if networks.returncode == 0 and networks.stdout.split()
        else subprocess.CompletedProcess([], networks.returncode, "[]", "")
    )
    if any(done.returncode != 0 for done in (containers, networks, inspected)):
        say(f"{slot}: {below}, and its daemon does not answer: tried again next minute")
        return
    try:
        found = json.loads(inspected.stdout)
    except ValueError as error:
        raise MachineError("the daemon's networks are not JSON") from error
    if workflow_running(containers.stdout, found, time.time()):
        say(f"{slot}: {below}, and a workflow is running: tried again next minute")
        return
    if not stopped.exists():
        run("systemctl", "stop", agent, capture=True)
        ROOM_STATE.mkdir(parents=True, exist_ok=True)
        stopped.touch()
        say(f"{slot}: {below}, and idle, so its agent stopped")
    for what in ("container", "network", "image"):
        docker_as(grader, slot, what, "prune", "--force", timeout=600)
    docker_as(grader, slot, "volume", "prune", "--all", "--force", timeout=600)
    free, mark = room()
    cleaned = f"cleaned up to {free / 2**30:.2f} GiB free"
    if free < mark:
        say(f"{slot}: {cleaned}, so its agent stays stopped")
        return
    run("systemctl", "start", agent, capture=True)
    stopped.unlink()
    say(f"{slot}: {cleaned}, so its agent started")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grading-machine.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "self-test"):
        commands.add_parser(name).add_argument("settings", type=Path)
    room = commands.add_parser("room")
    room.add_argument("--grader", required=True)
    room.add_argument("slots", nargs="+")
    options = parser.parse_args(argv)
    try:
        if options.command == "room":
            if os.geteuid() != 0:
                raise MachineError("run as root")
            try:
                grader = pwd.getpwnam(options.grader)
            except KeyError as error:
                raise MachineError(f"no user {options.grader}") from error
            if grader.pw_uid == 0:
                raise MachineError(f"{options.grader} is root, not the throwaway user")
            # One slot that cannot be looked at keeps none of the others
            # from it.
            failed = False
            for slot in options.slots:
                try:
                    check_room(grader, slot)
                except MachineError as error:
                    say(f"{slot}: {error}")
                    failed = True
            return 1 if failed else 0
        settings = Settings.read(options.settings)
        if options.command == "setup":
            return setup(settings)
        if os.geteuid() != 0:
            raise MachineError("run as root")
        return self_test(settings)
    except MachineError as error:
        print(f"grading-machine: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
