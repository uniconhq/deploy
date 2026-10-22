#!/usr/bin/env python3
"""Make a machine a grading machine, and check that it is one.

Run as root on Ubuntu 24.04 at enrolment, with the machine's settings in a
JSON file (see `Settings`):

    python3 grading-machine.py setup /etc/unicon/machine.json
    python3 grading-machine.py self-test /etc/unicon/machine.json

It builds the one shape phase 4.1 picked, provisionally: rootless Docker
under a throwaway user, and nothing else takes a step. In order, `setup`:

1. makes the throwaway user, which owns the rootless daemon, lingering on
   so its daemon runs without a login, with cpu, cpuset, io, memory and
   pids delegated to every user's systemd (#94), and the agent's own
   user, a member of the throwaway user's group so it reaches the
   daemon's socket and nothing of the throwaway user's traffic is its
   own (open 18);
2. allows `rootlesskit` its user namespace where AppArmor restricts them,
   as Ubuntu 24.04 does (phase 4.1 point 1);
3. checks that Docker's data root is a filesystem of its own, so a full
   disk of images and caches stops at its size (#507 item 5);
4. writes `/etc/unicon/hosts`, the names a step sees: the forge's and
   Garage's addresses and no other, and pins every name the daemon and
   the agent use in `/etc/hosts`, since under the wall no lookup of the
   throwaway user's is answered (open 17);
5. gives the rootless daemon its daemon.json: `"dns"` at an address that
   answers nothing, so a container's lookup goes nowhere (phase 4.1
   check 12), the data root, the socket the agent reaches, and any
   registry it may pull from over plain HTTP;
6. installs and starts the rootless daemon, and pulls the images named
   in the settings while the machine can still reach them;
7. puts the wall up: an nftables `inet` table on the throwaway user's
   uid, which holds for IPv4 and IPv6 alike, letting its traffic reach
   only the addresses the settings allow (phase 4.1 point 7), loaded at
   every boot before the user's systemd starts;
8. runs the self-test, and only when it passes starts the agent: as its
   own user, against the rootless socket, with `/etc/unicon/hosts` as
   every step's `/etc/hosts`, the memory a step's container may use, its
   pool label and its shape's label.

Run again, it stops the agent, takes the wall down for its pulls, and
puts everything back, so it is how a machine takes new settings, and how
the pinned addresses follow the registry's when they change (open 17).

`self-test` checks, from a container of the rootless daemon on a network
made as Woodpecker makes one per pipeline: that it reaches the first
address the wall allows; that it reaches none of the outside addresses
in the settings, over IPv4 or IPv6, and none of the machine's own; that
a lookup gets no answer; that a container's memory and process limits
bite, which a box's limits need (#94); that a privileged container, as
an exec step is, can open no block device; that the daemon is rootless
on cgroup v2 with the controllers delegated; and the kernel settings a
box's syscall filter leaves to the machine (opens 26 and 30). It prints
"network wall: on" or "network wall: off", and exits 1 when any check
fails.

Not yet run on a real machine: everything here ran on a stand-in, an
Ubuntu 24.04 container booting systemd over Docker Desktop's kernel,
which has no AppArmor, so step 2 has not run. The production enrolment
(the worker, feature 12) is to run this script. It stays within Python
3.12's standard library, the one Ubuntu 24.04 ships.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
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
WALL_FILE = UNICON / "wall.nft"
AGENT_ENV = UNICON / "agent.env"
AGENT_BINARY = Path("/usr/local/bin/woodpecker-agent")
AGENT_STATE = Path("/var/lib/unicon-agent")
UNITS = Path("/etc/systemd/system")
HOSTS_BEGIN = "# unicon machine names: begin"
HOSTS_END = "# unicon machine names: end"
APPARMOR_SWITCH = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
APPARMOR_PROFILE = Path("/etc/apparmor.d/unicon-rootlesskit")
REQUIRED_TOOLS = (
    "nft",
    "systemctl",
    "loginctl",
    "runuser",
    "newuidmap",
    "dockerd-rootless-setuptool.sh",
    "docker",
)
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


class MachineError(Exception):
    """A setting or a step that leaves the machine unfit to grade."""


@dataclass(frozen=True)
class Target:
    """An address and a TCP port, IPv4 or IPv6."""

    address: str
    port: int

    @classmethod
    def parse(cls, text: str) -> Target:
        """`1.2.3.4:80` or `[fd41::40]:80`."""
        if text.startswith("["):
            address, _, port = text[1:].partition("]:")
        else:
            address, _, port = text.rpartition(":")
        try:
            ipaddress.ip_address(address)
            number = int(port)
        except ValueError as error:
            raise MachineError(f"{text!r} is not an address and a port") from error
        return cls(address, number)

    @property
    def family(self) -> str:
        return "ip6" if ":" in self.address else "ip"

    def __str__(self) -> str:
        if self.family == "ip6":
            return f"[{self.address}]:{self.port}"
        return f"{self.address}:{self.port}"


@dataclass(frozen=True)
class Settings:
    """What the machine is told at enrolment. `server`, `agent_secret_file`,
    `agent_image`, `data_root`, `step_hosts`, `wall_allow` and `test_image`
    have no default.

    `step_hosts` is every step's `/etc/hosts`: the forge's and Garage's
    names and addresses. `machine_hosts` is pinned in the machine's own
    `/etc/hosts`: every name the daemon pulls from and the agent dials.
    `wall_allow` is every address and port the throwaway user's traffic
    may reach, the step's and the daemon's alike; the first is the one the
    self-test expects a step to reach. `outside` is where the self-test
    expects a step not to get. `pull` is pulled at setup, before the wall
    goes up: the test image, and whatever a pipeline runs that the wall
    keeps the daemon from pulling later. `agent_environment` is passed to
    the agent as it is, after the variables this script sets.
    """

    server: str
    agent_secret_file: Path
    agent_image: str
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
    limit_mem: int | None = None
    max_workflows: int = 4
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
            values[name] = Path(str(values[name]))
        for name in ("wall_allow", "outside"):
            if name in values:
                values[name] = tuple(Target.parse(str(t)) for t in _list(values, name))
        for name in ("pull", "insecure_registries"):
            if name in values:
                values[name] = tuple(str(item) for item in _list(values, name))
        settings = cls(**values)  # type: ignore[arg-type]
        settings.check()
        return settings

    def check(self) -> None:
        if self.shape not in SHAPES:
            raise MachineError(
                f"shape {self.shape!r} is not built; the one shape is rootless"
            )
        if not self.wall_allow:
            raise MachineError("wall_allow is empty: a step could reach nothing")
        if self.grader_user == self.agent_user:
            raise MachineError("the agent runs as a user of its own (open 18)")
        if "@sha256:" not in self.agent_image:
            raise MachineError("agent_image is pinned by its digest")
        for hosts in (self.step_hosts, self.machine_hosts):
            for name, address in hosts.items():
                try:
                    ipaddress.ip_address(address)
                except ValueError as error:
                    raise MachineError(
                        f"{name} is pinned to {address!r}, not an address"
                    ) from error

    @property
    def labels(self) -> str:
        return f"pool={self.pool},shape={self.shape}"


def _list(values: dict[str, object], name: str) -> list[object]:
    value = values[name]
    if not isinstance(value, list):
        raise MachineError(f"{name} is a list")
    return value


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


def agent_environment(settings: Settings, secret: str, limit_mem: int) -> str:
    """The agent's environment file. Woodpecker reads the token from it, so
    it is written readable by root alone.
    """
    values = {
        "WOODPECKER_SERVER": settings.server,
        "WOODPECKER_AGENT_SECRET": secret,
        "WOODPECKER_HOSTNAME": socket.gethostname(),
        "WOODPECKER_AGENT_LABELS": settings.labels,
        "WOODPECKER_AGENT_CONFIG_FILE": str(AGENT_STATE / "agent.conf"),
        "WOODPECKER_MAX_WORKFLOWS": str(settings.max_workflows),
        "WOODPECKER_HEALTHCHECK_ADDR": "127.0.0.1:3000",
        "WOODPECKER_BACKEND": "docker",
        "DOCKER_HOST": f"unix://{SOCKET_DIRECTORY}/docker.sock",
        "WOODPECKER_BACKEND_DOCKER_VOLUMES": f"{STEP_HOSTS}:/etc/hosts:ro",
        "WOODPECKER_BACKEND_DOCKER_LIMIT_MEM": str(limit_mem),
        **settings.agent_environment,
    }
    for name, value in values.items():
        if "\n" in value:
            raise MachineError(f"{name} holds a line break")
    return "".join(f"{name}={value}\n" for name, value in values.items())


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
    return (
        "[Unit]\n"
        "Description=Unicon grading wall\n"
        "DefaultDependencies=no\n"
        "Before=network-pre.target user@" + str(uid) + ".service\n"
        "Wants=network-pre.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart=/usr/sbin/nft -f {WALL_FILE}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def agent_unit(settings: Settings, uid: int) -> str:
    return (
        "[Unit]\n"
        "Description=Unicon grading agent\n"
        f"After=network-online.target unicon-wall.service user@{uid}.service\n"
        f"Requires=unicon-wall.service\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"User={settings.agent_user}\n"
        f"Group={settings.agent_user}\n"
        f"SupplementaryGroups={settings.grader_user}\n"
        f"EnvironmentFile={AGENT_ENV}\n"
        f"ExecStart={AGENT_BINARY}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "NoNewPrivileges=yes\n"
        "ProtectSystem=strict\n"
        "ProtectHome=yes\n"
        "PrivateTmp=yes\n"
        f"ReadWritePaths={AGENT_STATE}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
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


def docker(*arguments: str, check: bool = True, timeout: float = 300) -> str:
    """The rootless daemon, through the agent's socket, as root."""
    done = run(
        "docker",
        "-H",
        f"unix://{SOCKET_DIRECTORY}/docker.sock",
        *arguments,
        check=check,
        capture=True,
        timeout=timeout,
    )
    return done.stdout


def write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(text)
    temporary.chmod(mode)
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

    run("systemctl", "stop", "unicon-agent.service", check=False, capture=True)
    grader = make_users(settings)
    uid = grader.pw_uid
    allow_rootlesskit()
    check_data_root(settings.data_root, grader)

    write(STEP_HOSTS, step_hosts(settings.step_hosts))
    hosts = Path("/etc/hosts")
    # In place, not renamed over: in a container, as the stand-in is,
    # /etc/hosts is a mount, which a rename cannot replace.
    hosts.write_text(pinned_hosts(hosts.read_text(), settings.machine_hosts))
    write(
        SOCKET_TMPFILES,
        f"d {SOCKET_DIRECTORY} 0750 {settings.grader_user} {settings.grader_user} -\n",
    )
    run("systemd-tmpfiles", "--create", str(SOCKET_TMPFILES))
    config = Path(grader.pw_dir) / ".config/docker"
    write(config / "daemon.json", daemon_json(settings, uid))
    run("chown", "-R", f"{grader.pw_uid}:{grader.pw_gid}", str(config.parent))

    run("nft", "delete", "table", "inet", WALL_TABLE, check=False, capture=True)
    start_daemon(settings, grader)
    for image in (settings.test_image, *settings.pull):
        pull(image)
    install_agent(settings)

    write(WALL_FILE, wall_table(uid, settings.wall_allow))
    write(UNITS / "unicon-wall.service", wall_unit(uid))
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "unicon-wall.service", capture=True)
    run("systemctl", "restart", "unicon-wall.service", capture=True)

    limit = settings.limit_mem or memory_total()
    write(AGENT_ENV, agent_environment(settings, secret, limit), mode=0o600)
    AGENT_STATE.mkdir(mode=0o700, exist_ok=True)
    agent = pwd.getpwnam(settings.agent_user)
    os.chown(AGENT_STATE, agent.pw_uid, agent.pw_gid)
    write(UNITS / "unicon-agent.service", agent_unit(settings, uid))
    run("systemctl", "daemon-reload")

    if self_test(settings) != 0:
        say("the self-test failed, so the agent is not started")
        return 1
    run("systemctl", "enable", "--now", "unicon-agent.service", capture=True)
    say(f"agent started, labels {settings.labels}, memory limit {limit} bytes")
    return 0


def make_users(settings: Settings) -> pwd.struct_passwd:
    for user in (settings.grader_user, settings.agent_user):
        try:
            pwd.getpwnam(user)
        except KeyError:
            say(f"making the user {user}")
            if user == settings.grader_user:
                run("useradd", "--create-home", "--shell", "/usr/sbin/nologin", user)
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


def subordinate_count(table: Path, user: str) -> int:
    total = 0
    for line in table.read_text().splitlines():
        name, _, rest = line.partition(":")
        if name == user:
            total += int(rest.split(":")[1])
    return total


def allow_rootlesskit() -> None:
    if not APPARMOR_SWITCH.exists() or APPARMOR_SWITCH.read_text().strip() != "1":
        say("AppArmor does not restrict user namespaces here: no profile needed")
        return
    write(APPARMOR_PROFILE, rootlesskit_profile())
    run("apparmor_parser", "--replace", str(APPARMOR_PROFILE))
    say("AppArmor: rootlesskit may make its user namespace")


def check_data_root(root: Path, grader: pwd.struct_passwd) -> None:
    if not root.is_dir() or not os.path.ismount(root):
        raise MachineError(
            f"{root} is not a filesystem of its own; mount one there, so a "
            "disk filled with images and caches stops at its size"
        )
    os.chown(root, grader.pw_uid, grader.pw_gid)
    root.chmod(0o700)
    usage = shutil.disk_usage(root)
    say(f"data root {root}: {usage.total // 2**30} GiB of its own")


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
    throwaway user's daemon, whose traffic the wall would hold.
    """
    pull(settings.agent_image)
    container = docker("create", settings.agent_image).strip()
    try:
        docker("cp", f"{container}:/bin/woodpecker-agent", str(AGENT_BINARY))
    finally:
        docker("rm", container, check=False)
    os.chown(AGENT_BINARY, 0, 0)
    AGENT_BINARY.chmod(0o755)


def memory_total() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise MachineError("cannot read the machine's memory")


# The self-test.

PROBE = """
import json, socket, sys
out = {}
for spec in json.loads(sys.argv[1]):
    address, port = spec
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(4)
    try:
        s.connect((address, port))
        out[f"{address}:{port}"] = "connected"
    except OSError as error:
        out[f"{address}:{port}"] = type(error).__name__
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


def self_test(settings: Settings) -> int:
    report = Report()
    grader = pwd.getpwnam(settings.grader_user)
    say("self-test")
    wall_up = (
        run(
            "nft", "list", "table", "inet", WALL_TABLE, check=False, capture=True
        ).returncode
        == 0
    )
    report.check(wall_up, f"the nftables table {WALL_TABLE} is loaded")
    check_daemon(settings, grader, report)
    held = check_reach(settings, report)
    check_limits(settings, report)
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


def check_reach(settings: Settings, report: Report) -> bool:
    """From a container on a network made as Woodpecker makes one per
    pipeline. True when nothing outside the wall was reached.
    """
    network = "unicon-self-test"
    docker("network", "rm", network, check=False)
    docker("network", "create", "--ipv6", network, check=False)
    if network not in docker("network", "ls", "--format", "{{.Name}}"):
        docker("network", "create", network)
    held = True
    try:
        with listening() as port:
            own = [Target(address, port) for address in machine_addresses()]
            own.append(Target("10.0.2.2", port))
            allowed = settings.wall_allow[0]
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
            seen = json.loads(raw.strip().splitlines()[-1])
    finally:
        docker("network", "rm", network, check=False)

    def result(target: Target) -> str:
        return str(seen[f"{target.address}:{target.port}"])

    report.check(
        result(allowed) == "connected",
        f"a step reaches {allowed}, the first address the wall allows",
        result(allowed),
    )
    for target in settings.outside:
        held &= report.check(
            result(target) != "connected",
            f"a step does not reach {target} outside",
            result(target),
        )
    for target in own:
        held &= report.check(
            result(target) != "connected",
            f"a step does not reach the machine at {target}",
            result(target),
        )
    held &= report.check(
        seen["lookup"] != "answered", "a step's lookup gets no answer", seen["lookup"]
    )
    return held


def check_limits(settings: Settings, report: Report) -> None:
    limited = ("--memory", "64m", "--memory-swap", "64m", "--pids-limit", "32")
    hog = run(
        "docker",
        "-H",
        f"unix://{SOCKET_DIRECTORY}/docker.sock",
        "run",
        "--rm",
        *limited,
        "--entrypoint",
        "python3",
        settings.test_image,
        "-c",
        MEMORY_HOG,
        check=False,
        capture=True,
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
