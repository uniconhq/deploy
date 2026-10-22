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
`agent_binary_sha256`, and its slots worked out (`plan_slots`), before
anything on the machine is changed. A slot is a share of the machine of
one size of `slots.json`, kept beside this script, the one table of sizes
the forge plans each pipeline into too: cores of its own and a memory cap,
run by an agent of its own that takes one workflow at a time. The slots
are fixed until setup runs again. Then, in order, `setup`:

1. disables every agent, so nothing new is graded while the machine is
   set up;
2. makes the throwaway user, which owns the rootless daemon, lingering on
   so its daemon runs without a login, with cpu, cpuset, io, memory and
   pids delegated to every user's systemd, and the agent's own user, a
   member of the throwaway user's group so it reaches the daemon's socket
   and nothing of the throwaway user's traffic is its own;
3. kills every container of the throwaway user's daemon and stops the
   daemon, since a step's container outlives its agent, and stops there,
   the wall still up, if the daemon still answers; only then takes the
   wall down; and disables the machine's root Docker, whose `docker`
   group is root on the machine and whose containers no wall holds;
4. allows `rootlesskit` its user namespace where AppArmor restricts them,
   as Ubuntu 24.04 does;
5. checks that Docker's data root is a filesystem of its own, so a full
   disk of images and caches stops at its size;
6. counts out-of-memory kills in each cgroup alone (`memory_localevents`),
   at every boot before the throwaway user's systemd starts, so a box's
   kill does not mark the step container killed, which Woodpecker would
   fail the step for;
7. writes `/etc/unicon/hosts`, the names a step sees: the forge's and
   Garage's addresses and no other, and pins every name the daemon and
   the agent use in `/etc/hosts`, since under the wall no lookup of the
   throwaway user's is answered; and `/etc/unicon/cache-cap`, the most the
   step cache may hold;
8. gives the rootless daemon its daemon.json, written as the throwaway
   user, whose folder it is: `"dns"` at an address that answers nothing,
   so a container's lookup goes nowhere, the data root, the socket the
   agent reaches, and any registry it may pull from over plain HTTP;
9. installs and starts the rootless daemon, pulls the images named in the
   settings while the machine can still reach them, and installs the
   agent's binary from its pinned image once its SHA-256 is the one the
   settings give;
10. puts the wall up: an nftables `inet` table on the throwaway user's
    uid, which holds for IPv4 and IPv6 alike, letting its traffic reach
    only the addresses the settings allow, loaded at every boot before
    the user's systemd starts, and again whenever `nftables.service`,
    whose rules file flushes every table, is restarted or reloaded;
11. writes each slot's agent's environment, removing those of slots gone,
    and caps the throwaway user's slice, which holds every container, at
    the machine's memory less what the system and the agents keep
    (`slice_drop_in`);
12. runs the self-test, and only when it passes enables every slot's
    agent: as its own user, against the rootless socket, starting only
    while the wall is loaded, with `/etc/unicon/hosts` as every step's
    `/etc/hosts`, the cache's cap, its slot's cores and memory on every
    container, and its pool, shape, size and lane labels.

Run again, it does the same, so it is how a machine takes new settings,
and how the pinned addresses follow the registry's when they change.

`self-test` checks, from a container of the rootless daemon on a network
made as Woodpecker makes one per pipeline: that it reaches the first
address the wall allows; that it reaches none of the outside addresses in
the settings, over IPv4 or IPv6, and none of the machine's own; and that a
lookup gets no answer. Then, as the throwaway user in the machine's own
network namespace, where the daemon's pulls leave: the outside addresses,
the machine's resolver at 127.0.0.53, and its loopback ports. Each target
is tried from root first, which the wall does not hold: one root does not
reach either is "not tested", not passed, and so is one a step has no
route to, since then no wall refused it; at least one outside address must
be tested. Then that a container's memory and process limits bite, which a
box's limits need; that the cgroup hierarchy kept every option it was
remounted with; that a container over its own memory is marked
killed and one whose child cgroup was killed is not; that a privileged
container, as an exec step is, can open no block device; that the daemon
is rootless on cgroup v2 with the controllers delegated; that the root
Docker is stopped; the kernel settings a box's syscall filter leaves
to the machine; that a container made with each slot's limits has its
cores alone, its memory and no swap; and that the slice is capped. It
prints "network wall: on" or "network wall: off", and exits 1 when any
check fails.

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
from pathlib import Path

SHAPES = ("rootless",)
DELEGATED = ("cpu", "cpuset", "io", "memory", "pids")
WALL_TABLE = "unicon-wall"
NOWHERE = "192.0.2.1"
SOCKET_DIRECTORY = Path("/run/unicon-docker")
SOCKET_TMPFILES = Path("/etc/tmpfiles.d/unicon-docker.conf")
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
SYSTEM_MB = 2048
"""What a machine keeps of its memory, in MB, outside the throwaway user's
slice, which every container is in: the system and the agents."""
DAEMON_MB = 1024
"""What the slice keeps beside its slots, in MB: the rootless daemon,
containerd and the network helper."""
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
    "docker",
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
    pinned to and the memory every container of its workflow is capped
    at, in MB."""

    name: str
    cores: int
    memory_mb: int


@dataclass(frozen=True)
class Slot:
    """One slot of the machine and its agent: its name, `<size>-<n>`, its
    size, the cores it is pinned to as cpuset names them, whether it is
    kept for the early lane, and its agent's health check port."""

    name: str
    size: Size
    cpus: str
    early: bool
    port: int

    @property
    def memory(self) -> int:
        """Its memory in bytes."""
        return self.size.memory_mb * 2**20

    def labels(self, settings: Settings) -> str:
        """Its agent's labels. The forge labels a pipeline with the size it
        fits and its lane; a slot kept for the early lane takes early
        pipelines alone, and every other slot takes either, `*`, so a late
        slot of each size is never kept from the early lane's work."""
        lane = "early" if self.early else "*"
        return f"{settings.labels},size={self.size.name},lane={lane}"


def read_sizes(text: str) -> tuple[Size, ...]:
    """The sizes of `SLOT_TABLE`, `{name: {cores, memory_mb}}`, smallest
    first."""
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
            or set(size) != {"cores", "memory_mb"}
            or not all(whole(value) and value >= 1 for value in size.values())
        ):
            raise MachineError(
                f"{SLOT_TABLE} gives {name!r} no whole cores and memory_mb, at least 1"
            )
        sizes.append(Size(name, size["cores"], size["memory_mb"]))
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
    sizes: Sequence[Size], settings: Settings, cpus: Sequence[int], memory_mb: int
) -> tuple[Slot, ...]:
    """The machine's slots, fixed until setup runs again, from its `cpus`
    and `memory_mb`, less `SYSTEM_CORES`, `SYSTEM_MB` and `DAEMON_MB`.
    `settings.slots` gives how many of each size; otherwise one of each,
    the largest first, while it fits, and then as many of the smallest as
    fit. Of the smallest size there is, `early_share` of every slot,
    rounded, is kept for the early lane, but one of that size is not, so
    late work of each size has a slot. Each slot gets cores of its own.
    Refused when the slots asked for do not fit, or none does.
    """
    free_cpus = sorted(cpus)[SYSTEM_CORES:]
    free_mb = memory_mb - SYSTEM_MB - DAEMON_MB
    by_name = {size.name: size for size in sizes}
    unknown = sorted(set(settings.slots) - set(by_name))
    if unknown:
        raise MachineError(
            f"slots names {', '.join(unknown)}, which {SLOT_TABLE.name} does not: "
            f"{', '.join(by_name)}"
        )
    counts = {size.name: settings.slots.get(size.name, 0) for size in sizes}
    if not settings.slots:
        cores, held = 0, 0
        for size in (*reversed(sizes), *[sizes[0]] * len(free_cpus)):
            if (
                cores + size.cores <= len(free_cpus)
                and held + size.memory_mb <= free_mb
            ):
                counts[size.name] += 1
                cores += size.cores
                held += size.memory_mb
    cores = sum(by_name[name].cores * count for name, count in counts.items())
    held = sum(by_name[name].memory_mb * count for name, count in counts.items())
    if not any(counts.values()) or cores > len(free_cpus) or held > free_mb:
        raise MachineError(
            f"the slots ({', '.join(f'{n} {name}' for name, n in counts.items())}) "
            f"need {cores} cores and {held} MB; this machine gives slots "
            f"{len(free_cpus)} cores and {free_mb} MB"
        )
    total = sum(counts.values())
    smallest = next(size.name for size in sizes if counts[size.name])
    early = min(round(total * settings.early_share), counts[smallest] - 1)
    slots: list[Slot] = []
    for size in sizes:
        for number in range(1, counts[size.name] + 1):
            taken, free_cpus = free_cpus[: size.cores], free_cpus[size.cores :]
            slots.append(
                Slot(
                    f"{size.name}-{number}",
                    size,
                    cpu_list(taken),
                    size.name == smallest and number <= early,
                    FIRST_HEALTH_PORT + len(slots),
                )
            )
    return tuple(slots)


@dataclass(frozen=True)
class Settings:
    """What the machine is told at enrolment. `server`, `agent_secret_file`,
    `agent_image`, `agent_binary_sha256`, `data_root`, `step_hosts`,
    `wall_allow` and `test_image` have no default.

    `agent_binary_sha256` is the SHA-256 of `/bin/woodpecker-agent` in
    `agent_image`, by the machine's architecture as `uname -m` names it,
    `x86_64` or `aarch64`: the binary is copied out of an image the
    throwaway user's daemon holds, so it runs only as pinned. `test_image`,
    which the self-test runs privileged, is pinned by its digest, as
    `agent_image` is. `grader_user` and `agent_user` differ, and each is a
    user setup makes, or made before (`taken`): not root, a user who can
    log in, or a system user of another's. `pool` is a label's value: letters,
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
    `cache_cap` is the most the step cache may hold, in bytes; a quarter
    of the data root's size when not given. `slots` gives how many slots
    of each size of `SLOT_TABLE` the machine runs, and `early_share` the
    share of them kept for the early lane (`plan_slots`); the sizes
    themselves are the table's alone, which the forge plans with.
    `agent_environment` is passed to every agent as it is, after the
    variables this script sets, but for those a slot is made of.
    """

    server: str
    agent_secret_file: Path
    agent_image: str
    agent_binary_sha256: dict[str, str]
    data_root: Path
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
    cache_cap: int | None = None
    slots: dict[str, int] = field(default_factory=dict)
    early_share: float = 0.25
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
                "data_root",
                "step_hosts",
                "wall_allow",
                "test_image",
            )
            if name not in raw
        )
        if missing:
            raise MachineError(f"missing settings: {', '.join(missing)}")
        values = dict(raw)
        for name in ("agent_secret_file", "data_root"):
            if not isinstance(values[name], str):
                raise MachineError(f"{name} is a path, as a text")
            values[name] = Path(str(values[name]))
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
        for name, home in (
            ("grader_user", Path("/home") / self.grader_user),
            ("agent_user", AGENT_STATE),
        ):
            user = getattr(self, name)
            if not USER_NAME.fullmatch(user) or user == "root" or taken(user, home):
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
        if (
            not isinstance(self.early_share, int | float)
            or isinstance(self.early_share, bool)
            or not 0 <= self.early_share < 1
        ):
            raise MachineError("early_share is a number from 0 to below 1")
        if self.cache_cap is not None and (
            not whole(self.cache_cap) or self.cache_cap < 2**30
        ):
            raise MachineError("cache_cap is a number of bytes, at least 1 GiB")
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


def taken(user: str, home: Path) -> bool:
    """Whether `user` is a user of the machine already that setup did not
    make: root, one whose shell lets them log in, or one whose home is not
    `home`, the one setup gives it (`make_users`). Setup puts the agent's
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


def pinned_hosts(current: str, names: dict[str, str]) -> str:
    """The machine's `/etc/hosts` with our block replaced by these names, the
    rest left as it was.
    """
    kept: list[str] = []
    inside = False
    for line in current.splitlines():
        if line == HOSTS_BEGIN:
            inside = True
        elif line == HOSTS_END:
            inside = False
        elif not inside:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    block = [HOSTS_BEGIN]
    block += [f"{address} {name}" for name, address in sorted(names.items())]
    block.append(HOSTS_END)
    return "\n".join([*kept, "", *block]) + "\n"


def daemon_json(settings: Settings, uid: int) -> str:
    document: dict[str, object] = {
        "dns": [NOWHERE],
        "data-root": str(settings.data_root),
        # The sockets' group. The daemon's default is `docker`, whose id
        # inside its user namespace lands on a subordinate id outside, so
        # the agent could not reach the socket. Group 0 inside is the
        # throwaway user's own group outside, of which the agent's user
        # is a member.
        "group": "root",
        "hosts": [
            f"unix:///run/user/{uid}/docker.sock",
            f"unix://{SOCKET_DIRECTORY}/docker.sock",
        ],
    }
    if settings.insecure_registries:
        document["insecure-registries"] = list(settings.insecure_registries)
    return json.dumps(document, indent=2) + "\n"


def agent_environment(settings: Settings, secret: str, slot: Slot) -> str:
    """A slot's agent's environment file. Woodpecker reads the token from
    it, so it is written readable by root alone. The agent takes one
    workflow at a time, under its slot's labels, a name and a state folder
    of its own; every container of it is pinned to the slot's cores and
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
        "DOCKER_HOST": f"unix://{SOCKET_DIRECTORY}/docker.sock",
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


def cache_cap(settings: Settings, data_root_size: int) -> int:
    """The cache's cap: the setting, or a quarter of the data root, which
    the images and every step's own files share with it."""
    return settings.cache_cap or data_root_size // 4


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
    starting only while the wall's table is loaded and once the cgroup
    option is set, each with its own environment and state folder.
    """
    return (
        "[Unit]\n"
        "Description=Unicon grading agent of slot %i\n"
        f"After=network-online.target unicon-wall.service unicon-cgroup.service "
        f"user@{uid}.service\n"
        "Requires=unicon-wall.service unicon-cgroup.service\n"
        "Wants=network-online.target\n\n"
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


def slice_memory(memory_mb: int) -> int:
    """The throwaway user's slice's cap, in bytes: the machine's memory less
    `SYSTEM_MB`. Every container of every slot is in it, with the daemon,
    so all of them together leave the system and the agents their room
    whatever a step does."""
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


def as_user(user: str, uid: int, *command: str, **options: object) -> str:
    environment = (
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    )
    done = run(
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
    return done.stdout


def docker_done(
    *arguments: str, check: bool = True, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    """The rootless daemon, through the agent's socket, as root."""
    return run(
        "docker",
        "-H",
        f"unix://{SOCKET_DIRECTORY}/docker.sock",
        *arguments,
        check=check,
        capture=True,
        timeout=timeout,
    )


def docker(*arguments: str, check: bool = True, timeout: float = 300) -> str:
    return docker_done(*arguments, check=check, timeout=timeout).stdout


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
            "docker-ce-rootless-extras, uidmap, nftables and systemd"
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
    stop_daemon(settings, uid)
    run("systemctl", "stop", "unicon-wall.service", check=False, capture=True)
    disable_root_docker()
    allow_rootlesskit()
    size = check_data_root(settings.data_root, grader)
    count_kills_locally(uid)

    write(STEP_HOSTS, step_hosts(settings.step_hosts))
    write(CACHE_CAP, f"{cache_cap(settings, size)}\n")
    # In place, not renamed over: in a container, as the stand-in is,
    # /etc/hosts is a mount, which a rename cannot replace.
    HOSTS.write_text(pinned_hosts(HOSTS.read_text(), settings.machine_hosts))
    write(
        SOCKET_TMPFILES,
        f"d {SOCKET_DIRECTORY} 0750 {settings.grader_user} {settings.grader_user} -\n",
    )
    run("systemd-tmpfiles", "--create", str(SOCKET_TMPFILES))
    # As the throwaway user, whose folder it is: root writing there would
    # follow whatever links that user left in it.
    as_user(
        settings.grader_user,
        uid,
        "sh",
        "-c",
        'umask 077 && mkdir -p "$1" && cat > "$1/daemon.json.new" '
        '&& mv -f "$1/daemon.json.new" "$1/daemon.json"',
        "sh",
        str(Path(grader.pw_dir) / ".config/docker"),
        stdin=daemon_json(settings, uid),
    )

    start_daemon(settings, grader)
    for image in (settings.test_image, *settings.pull):
        pull(image)
    install_agent(settings)

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
    run("systemctl", "daemon-reload")

    if self_test(settings) != 0:
        say("the self-test failed, so no agent is enabled")
        return 1
    run("systemctl", "enable", "--now", *map(agent_name, slots), capture=True)
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
    """Every agent disabled and stopped: the one before slots, each slot's
    enabled, one by one, as a unit missing fails the whole call, and any
    slot's still running."""
    wants = UNITS / "multi-user.target.wants"
    enabled = sorted(path.name for path in wants.glob("unicon-agent@*.service"))
    for unit in (OLD_AGENT, *enabled):
        run("systemctl", "disable", "--now", unit, check=False, capture=True)
    run("systemctl", "stop", "unicon-agent@*.service", check=False, capture=True)


def machine_cpus() -> list[int]:
    """The machine's online CPUs, as cpuset numbers them."""
    return read_cpu_list(ONLINE_CPUS.read_text())


def machine_slots(settings: Settings) -> tuple[Slot, ...]:
    """This machine's slots, from `SLOT_TABLE`, its CPUs and its memory."""
    try:
        text = SLOT_TABLE.read_text()
    except OSError as error:
        raise MachineError(
            f"cannot read {SLOT_TABLE}, the slot sizes, kept beside this script: "
            f"{error}"
        ) from error
    return plan_slots(
        read_sizes(text), settings, machine_cpus(), memory_total() // 2**20
    )


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


def stop_daemon(settings: Settings, uid: int) -> None:
    """Kill every container of the throwaway user's daemon, then stop the
    daemon, before the wall comes down: a step's container outlives its
    agent. The daemon starts again only once the settings are in place.
    Refuses to go on, the wall still up, while the daemon answers."""
    running = docker("ps", "--quiet", check=False, timeout=60).split()
    if running:
        docker("kill", *running, check=False, timeout=60)
    as_user(
        settings.grader_user,
        uid,
        "systemctl",
        "--user",
        "stop",
        "docker.service",
        check=False,
        timeout=120,
    )
    # With no daemon to answer, the CLI still prints an empty line.
    if docker(
        "info", "--format", "{{.ServerVersion}}", check=False, timeout=60
    ).strip():
        raise MachineError(
            "the throwaway user's daemon did not stop, so the wall stays up"
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


def check_data_root(root: Path, grader: pwd.struct_passwd) -> int:
    """The data root's size, once it is a filesystem of its own and the
    throwaway user's."""
    if not root.is_dir() or not os.path.ismount(root):
        raise MachineError(
            f"{root} is not a filesystem of its own; mount one there, so a "
            "disk filled with images and caches stops at its size"
        )
    os.chown(root, grader.pw_uid, grader.pw_gid)
    root.chmod(0o700)
    usage = shutil.disk_usage(root)
    say(f"data root {root}: {usage.total // 2**30} GiB of its own")
    return usage.total


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


def start_daemon(settings: Settings, grader: pwd.struct_passwd) -> None:
    user, uid = settings.grader_user, grader.pw_uid
    unit = Path(grader.pw_dir) / ".config/systemd/user/docker.service"
    if not unit.exists():
        say("installing rootless Docker")
        as_user(user, uid, "dockerd-rootless-setuptool.sh", "install", timeout=300)
    as_user(user, uid, "systemctl", "--user", "enable", "docker.service")
    # rootlesskit copies /etc at the daemon's start, so the names pinned
    # above reach it only through a restart.
    as_user(user, uid, "systemctl", "--user", "reset-failed", "docker.service")
    as_user(user, uid, "systemctl", "--user", "restart", "docker.service")
    for _ in range(60):
        if docker("info", "--format", "{{.ServerVersion}}", check=False).strip():
            return
        time.sleep(1)
    raise MachineError(f"the rootless daemon did not answer on {SOCKET_DIRECTORY}")


def pull(image: str, tries: int = 3) -> None:
    """A pull, tried again a few seconds later, since a registry that drops
    one request, or a daemon that has just started, should not stop an
    enrolment.
    """
    say(f"pulling {image}")
    for attempt in range(tries):
        try:
            docker("pull", "-q", image, timeout=900)
            return
        except MachineError:
            if attempt == tries - 1:
                raise
            time.sleep(5)


def install_agent(settings: Settings) -> None:
    """The agent binary, copied out of its pinned image, so the agent runs
    on the machine as its own user rather than in a container of the
    throwaway user's daemon, whose traffic the wall would hold. The daemon
    is the throwaway user's, so what it hands over is installed only as a
    plain file with the SHA-256 the settings pin for this architecture.
    """
    expected = settings.agent_binary_sha256[architecture()]
    pull(settings.agent_image)
    container = docker("create", settings.agent_image).strip()
    staging = Path(
        tempfile.mkdtemp(prefix=".woodpecker-agent-", dir=AGENT_BINARY.parent)
    )
    try:
        copied = staging / AGENT_BINARY.name
        docker("cp", f"{container}:/bin/woodpecker-agent", str(copied))
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
        docker("rm", container, check=False)
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
    check_daemon(settings, grader, report)
    check_root_docker(report)
    held = check_reach(settings, grader, slots, report)
    check_limits(settings, report)
    check_slots(settings, grader, slots, report)
    check_kills(settings, report)
    check_block_devices(settings, report)
    check_kernel(report)
    say(f"network wall: {'on' if wall_up and held else 'off'}")
    if report.failed:
        say(f"{len(report.failed)} checks failed")
        return 1
    return 0


def check_daemon(settings: Settings, grader: pwd.struct_passwd, report: Report) -> None:
    info = json.loads(docker("info", "--format", "{{json .}}"))
    options = " ".join(info.get("SecurityOptions") or [])
    report.check("name=rootless" in options, "the daemon is rootless", options)
    report.check(
        info.get("CgroupVersion") == "2" and info.get("CgroupDriver") == "systemd",
        "the daemon runs on cgroup v2 under systemd",
        f"{info.get('CgroupDriver')} v{info.get('CgroupVersion')}",
    )
    root = Path(str(info.get("DockerRootDir")))
    report.check(
        root == settings.data_root and os.path.ismount(root),
        "Docker's data root is its own filesystem",
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
    report: Report,
) -> bool:
    """From a container on a network made as Woodpecker makes one per
    pipeline, then as the throwaway user in the machine's own network
    namespace, where the daemon's pulls leave, each target tried from root
    first. True when nothing the wall should hold got through.
    """
    network = "unicon-self-test"
    docker("network", "rm", network, check=False)
    docker("network", "create", "--ipv6", network, check=False)
    if network not in docker("network", "ls", "--format", "{{.Name}}"):
        docker("network", "create", network)
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
        docker("network", "rm", network, check=False)

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
    from inside a container made with them: its cores alone, its memory
    and no swap. And the throwaway user's slice capped as `slice_drop_in`
    says, over every container of every slot."""
    for slot in slots:
        seen = docker(
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


def check_limits(settings: Settings, report: Report) -> None:
    limited = ("--memory", "64m", "--memory-swap", "64m", "--pids-limit", "32")
    hog = docker_done(
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


def check_kills(settings: Settings, report: Report) -> None:
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
        docker("rm", "-f", name, check=False)
        try:
            ran = docker_done(
                "run", "--name", name, *arguments, check=False, timeout=120
            )
            marked = docker("inspect", "--format", "{{.State.OOMKilled}}", name).strip()
        finally:
            docker("rm", "-f", name, check=False)
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


def check_block_devices(settings: Settings, report: Report) -> None:
    opened = docker(
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grading-machine.py")
    parser.add_argument("command", choices=("setup", "self-test"))
    parser.add_argument("settings", type=Path)
    options = parser.parse_args(argv)
    try:
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
