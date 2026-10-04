"""Rendering .env from the template, which is the only list of keys."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from bootstrap import envfile

DEPLOY = Path(__file__).resolve().parent.parent

TEMPLATE = """# A comment
FILLED=
KEPT=from the template
# Another comment

EMPTY_AND_UNKNOWN=
"""


@pytest.fixture
def template(tmp_path: Path) -> Path:
    path = tmp_path / ".env.example"
    path.write_text(TEMPLATE, encoding="utf-8", newline="\n")
    return path


def test_fills_keys_and_keeps_everything_else(template: Path) -> None:
    rendered = envfile.render(template, {"FILLED": "a value"})

    assert rendered.splitlines() == [
        "# A comment",
        "FILLED=a value",
        "KEPT=from the template",
        "# Another comment",
        "",
        "EMPTY_AND_UNKNOWN=",
    ]


def test_a_value_overrides_a_template_default(template: Path) -> None:
    rendered = envfile.render(template, {"KEPT": "from .env"})

    assert "KEPT=from .env" in rendered.splitlines()


def test_keys_the_template_does_not_mention_are_appended(template: Path) -> None:
    rendered = envfile.render(template, {"FILLED": "x", "LATER": "y", "EARLIER": "z"})

    assert rendered.splitlines()[-3:] == [
        envfile.ADDED_OUTSIDE_TEMPLATE,
        "EARLIER=z",
        "LATER=y",
    ]


def test_no_marker_when_every_key_is_in_the_template(template: Path) -> None:
    rendered = envfile.render(template, {"FILLED": "x"})

    assert envfile.ADDED_OUTSIDE_TEMPLATE not in rendered


def test_a_missing_template_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(envfile.MissingTemplate):
        envfile.render(tmp_path / "absent", {})


def test_load_reads_assignments_and_ignores_the_rest(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# comment\n\nA=1\nnot an assignment\nB=has=signs\n", "utf-8")

    assert envfile.load(path) == {"A": "1", "B": "has=signs"}


def test_load_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert envfile.load(tmp_path / "absent") == {}


def test_write_uses_lf_on_every_platform(tmp_path: Path, template: Path) -> None:
    path = tmp_path / ".env"

    envfile.write(path, template, {"FILLED": "x"})

    assert b"\r" not in path.read_bytes()


def test_write_replaces_the_file_whole_and_leaves_no_partial_behind(
    tmp_path: Path, template: Path
) -> None:
    path = tmp_path / ".env"
    envfile.write(path, template, {"FILLED": "first"})

    envfile.write(path, template, {"FILLED": "second"})

    assert "FILLED=second" in path.read_text(encoding="utf-8").splitlines()
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [
        ".env",
        ".env.example",
    ]


def test_a_write_that_fails_keeps_the_previous_file(
    tmp_path: Path, template: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    envfile.write(path, template, {"FILLED": "first"})

    def refuse(*arguments: object, **options: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        envfile.write(path, template, {"FILLED": "second"})

    assert "FILLED=first" in path.read_text(encoding="utf-8").splitlines()


def test_a_key_the_template_gained_since_env_was_written_takes_its_default(
    template: Path,
) -> None:
    """A rerun over an older .env carries the new key at the template's
    default, and a value the older .env holds wins over the template's."""
    from bootstrap.main import _starting_values

    values = _starting_values(template, {"FILLED": "kept", "KEPT": "from .env"})

    assert values == {"FILLED": "kept", "KEPT": "from .env"}
    assert _starting_values(template, {}) == {"KEPT": "from the template"}


def test_a_rerun_drops_the_keys_nothing_reads(template: Path) -> None:
    """An older .env that still holds a retired key, set by hand or by an
    earlier bootstrap, loses it on the next run instead of carrying it under
    the added-outside-the-template marker."""
    from bootstrap.main import RETIRED, _starting_values

    previous = {key: "stale" for key in RETIRED} | {"FILLED": "kept"}

    values = _starting_values(template, previous)

    assert values == {"FILLED": "kept", "KEPT": "from the template"}
    assert envfile.ADDED_OUTSIDE_TEMPLATE not in envfile.render(template, values)


def test_no_retired_key_is_in_the_template_or_the_compose_files() -> None:
    from bootstrap.main import RETIRED

    template = envfile.load(DEPLOY / ".env.example")
    assert RETIRED.isdisjoint(template)
    for name in ("compose.yaml", "compose.dev.yaml"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        read = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", text))
        assert RETIRED.isdisjoint(read), name


def test_compose_names_what_bootstrap_makes() -> None:
    """compose.yaml writes the bucket name and the platform account into the
    backend's environment itself, so they have to be the ones bootstrap
    makes. The backend has one bucket: what people upload goes through the
    upload door into the one Forgejo owns."""
    from bootstrap import main

    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")

    assert "UNICON_S3_UPLOADS_BUCKET" not in text
    assert f"UNICON_S3_RESULTS_BUCKET: {main.RESULTS_BUCKET}\n" in text
    assert f"UNICON_FORGE_PLATFORM_ACCOUNT: {main.BACKEND_ACCOUNT}\n" in text
