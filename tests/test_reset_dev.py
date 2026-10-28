"""`scripts/reset-dev.py` refuses before it throws anything away."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reset-dev.py"
PEOPLE = ("alice", "bob", "carol", "dan", "erin")
KEYS = {
    key: value
    for name in PEOPLE
    for key, value in (
        (f"DEV_{name.upper()}_PASSWORD", f"{name}-password"),
        (f"DEV_{name.upper()}_EMAIL", f"{name}@example.com"),
    )
}


@pytest.fixture
def reset_dev(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The script as a module, its setting of `UNICON_E2E_URL` and `sys.path`
    undone after the test, so the browser tests stay skipped.
    """
    monkeypatch.setenv("UNICON_E2E_URL", "http://localhost:8080")
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("reset_dev", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_accounts_are_each_persons_password_and_address(
    reset_dev: ModuleType,
) -> None:
    found = reset_dev.accounts(KEYS)

    assert found == {
        name: (f"{name}-password", f"{name}@example.com") for name in PEOPLE
    }


def test_a_missing_or_empty_key_is_named(reset_dev: ModuleType) -> None:
    with pytest.raises(
        reset_dev.ResetFailed,
        match=r"\.env has no DEV_BOB_PASSWORD, DEV_BOB_EMAIL; nothing was changed",
    ):
        reset_dev.accounts(
            {key: value for key, value in KEYS.items() if key != "DEV_BOB_EMAIL"}
            | {"DEV_BOB_PASSWORD": ""}
        )


def test_a_missing_key_stops_the_reset_before_the_stack_is_touched(
    reset_dev: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".env").write_text(
        "".join(
            f"{key}={value}\n" for key, value in KEYS.items() if "_BOB_" not in key
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reset_dev, "DEPLOY", tmp_path)
    ran: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)

    assert reset_dev.main([]) == 1

    assert ran == []
    assert "DEV_BOB_PASSWORD, DEV_BOB_EMAIL" in capsys.readouterr().err
