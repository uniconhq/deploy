"""Running docker compose from Python.

Bootstrap starts services itself rather than telling the reader to run compose
between steps, because the order matters: Garage has to hold the Forgejo access
key before Forgejo starts using it for LFS, and Woodpecker has to hold its OAuth
client before it starts at all.

Nothing here ever puts the command line in an error or a log. Some of these
calls carry a password as an argument, and an exception that quoted the command
would put it in a terminal, a CI log and a bug report at once. Callers get the
verb, the service and whatever the command wrote to stderr.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DOCKER_SOCKET = "/var/run/docker.sock"


class ComposeFailed(Exception):
    """A docker compose command exited non-zero."""


class Compose:
    def __init__(self, project_dir: Path, files: Sequence[str]) -> None:
        self._project_dir = project_dir
        self._files = list(files)

    def _compose_command(self, arguments: Sequence[str]) -> list[str]:
        command = ["docker", "compose"]
        for file in self._files:
            command += ["-f", file]
        return command + list(arguments)

    def _run(
        self, description: str, command: Sequence[str], stdin: str | None = None
    ) -> str:
        """Bytes in and bytes out. A text pipe would turn every line break of
        what is written to stdin into the platform's own, and on Windows that
        puts a carriage return at the end of every line curl reads.
        """
        completed = subprocess.run(
            command,
            cwd=self._project_dir,
            input=stdin.encode("utf-8") if stdin is not None else None,
            capture_output=True,
        )
        if completed.returncode != 0:
            raise ComposeFailed(
                f"docker {description} exited {completed.returncode}\n"
                f"{completed.stderr.decode('utf-8', 'replace').strip()}"
            )
        return completed.stdout.decode("utf-8", "replace")

    def up(self, *services: str) -> None:
        self._run(
            f"compose up {' '.join(services)}",
            self._compose_command(["up", "-d", *services]),
        )

    def execute(
        self,
        service: str,
        *arguments: str,
        user: str | None = None,
        stdin: str | None = None,
    ) -> str:
        """Run a command inside a running service and return its stdout.

        `stdin` is how anything secret should be passed: `exec -T` connects the
        pipe, so a password in a SQL statement never becomes an argument.
        """
        options = ["-T"] + (["-u", user] if user else [])
        return self._run(
            f"compose exec {service} {arguments[0] if arguments else ''}".strip(),
            self._compose_command(["exec", *options, service, *arguments]),
            stdin=stdin,
        )

    def is_healthy(self, service: str) -> bool:
        """Whether the healthcheck declared in compose.yaml is passing."""
        for row in self._ps(service, include_stopped=False):
            return bool(row.get("Health") == "healthy")
        return False

    def is_running(self, service: str) -> bool:
        """Whether this service has a running container."""
        return bool(self._ps(service, include_stopped=False))

    def container_id(self, service: str) -> str | None:
        """The id of this service's container, running or stopped, or None
        when it has none. A recreated container has a new one.
        """
        for row in self._ps(service, include_stopped=True):
            return str(row["ID"])
        return None

    def volume_exists(self, name: str) -> bool:
        """Whether a named Docker volume is present. The name is the full one
        Docker holds, so it carries the compose project prefix."""
        listing = self._run(
            "volume ls", ["docker", "volume", "ls", "--format", "{{.Name}}"]
        )
        return name in listing.split()

    def ensure_image(self, reference: str) -> bool:
        """Pull an image this daemon does not hold yet, by its reference, and
        say whether it had to. An image already here is not pulled again, so a
        machine that has every image makes no call to a registry.
        """
        present = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
            cwd=self._project_dir,
            capture_output=True,
        )
        if present.returncode == 0:
            return False
        self._run("pull of a grading image", ["docker", "pull", "--quiet", reference])
        return True

    def socket_group(self, image: str) -> str:
        """The group id that owns the Docker socket, as a container on this
        daemon sees it: 0 on Docker Desktop, the docker group on Linux. A
        container that runs as an ordinary user reaches the socket only as a
        member of that group.
        """
        output = self._run(
            "run stat of the docker socket",
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--volume",
                f"{DOCKER_SOCKET}:{DOCKER_SOCKET}",
                image,
                "stat",
                "-c",
                "%g",
                DOCKER_SOCKET,
            ],
        )
        group = output.strip()
        if not group.isdigit():
            raise ComposeFailed(f"the docker socket's group is not a number: {group}")
        return group

    def _ps(self, service: str, include_stopped: bool) -> list[dict[str, Any]]:
        arguments = ["ps", "--format", "json"]
        if include_stopped:
            arguments.append("--all")
        listing = self._run(
            f"compose ps {service}", self._compose_command([*arguments, service])
        )
        return [json.loads(line) for line in listing.splitlines() if line.strip()]
