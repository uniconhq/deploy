"""A secret that is gone from .env is generated again only when no data on
the machine depends on it. If the volume that secret unlocks is still there,
the run stops and says which key and which volume.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap import main
from bootstrap.compose import Compose


class VolumesPresent(Compose):
    def __init__(self, present: set[str]) -> None:
        super().__init__(Path("."), [])
        self.present = present
        self.asked: list[str] = []

    def volume_exists(self, name: str) -> bool:
        self.asked.append(name)
        return name in self.present


COMPLETE = {key: "kept" for key in main.GENERATORS}
ENV = Path("deploy/.env")


def test_a_missing_env_over_the_forgejo_volume_is_refused() -> None:
    compose = VolumesPresent({"unicon_forgejo-data"})

    with pytest.raises(
        main.LostEnvironment, match=r"FORGEJO_SECRET_KEY.*unicon_forgejo-data"
    ):
        main._refuse_to_regenerate_over_existing_data(compose, ENV, {})


def test_one_deleted_line_over_its_volume_is_refused() -> None:
    compose = VolumesPresent({"unicon_forgejo-data", "unicon_postgres-data"})
    previous = dict(COMPLETE)
    del previous["UNICON_TOKEN_ENCRYPTION_KEY"]

    with pytest.raises(
        main.LostEnvironment, match=r"UNICON_TOKEN_ENCRYPTION_KEY.*unicon_postgres-data"
    ):
        main._refuse_to_regenerate_over_existing_data(compose, ENV, previous)


def test_an_empty_value_counts_as_missing() -> None:
    compose = VolumesPresent({"unicon_garage-meta"})

    with pytest.raises(main.LostEnvironment, match="GARAGE_RPC_SECRET"):
        main._refuse_to_regenerate_over_existing_data(
            compose, ENV, {**COMPLETE, "GARAGE_RPC_SECRET": ""}
        )


def test_a_missing_secret_whose_volume_is_gone_is_generated() -> None:
    compose = VolumesPresent({"unicon_forgejo-data"})
    previous = dict(COMPLETE)
    del previous["GARAGE_RPC_SECRET"]

    main._refuse_to_regenerate_over_existing_data(compose, ENV, previous)

    assert compose.asked == ["unicon_garage-meta"]


def test_a_secret_that_is_reapplied_on_every_run_is_not_guarded() -> None:
    compose = VolumesPresent({"unicon_forgejo-data", "unicon_postgres-data"})
    previous = dict(COMPLETE)
    del previous["FORGEJO_BACKEND_PASSWORD"]
    del previous["UNICON_DB_PASSWORD"]

    main._refuse_to_regenerate_over_existing_data(compose, ENV, previous)

    assert compose.asked == []


def test_a_complete_env_asks_nothing() -> None:
    compose = VolumesPresent({"unicon_forgejo-data"})

    main._refuse_to_regenerate_over_existing_data(compose, ENV, COMPLETE)

    assert compose.asked == []


def test_every_guarded_key_is_one_bootstrap_generates() -> None:
    assert set(main.UNLOCKS) <= set(main.GENERATORS)
