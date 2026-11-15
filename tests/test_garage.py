"""The Garage client's admin API calls."""

from __future__ import annotations

import json
from typing import Any

from bootstrap.garage import Garage


class Recording:
    """Stands in for compose: keeps every admin API call and answers none."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, service: str, *arguments: str) -> str:
        self.calls.append((arguments[2], json.loads(arguments[3])))
        return ""


def test_temporary_uploads_expire_and_unfinished_parts_are_aborted_after_one_day() -> (
    None
):
    compose = Recording()
    Garage(compose, "garage").expire_uploads("uploads")  # type: ignore[arg-type]
    assert compose.calls == [
        (
            "UpdateBucket",
            {
                "id": "uploads",
                "body": {
                    "lifecycleRules": [
                        {
                            "ID": "abandoned-uploads",
                            "Status": "Enabled",
                            "Filter": {"Prefix": ""},
                            "AbortIncompleteMultipartUpload": {
                                "DaysAfterInitiation": 1
                            },
                            "Expiration": {"Days": 1},
                        }
                    ]
                },
            },
        )
    ]
