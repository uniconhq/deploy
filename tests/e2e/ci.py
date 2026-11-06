"""Reading the CI's own record of a grading: its Woodpecker pipelines, as
the CI's administrator `unicon-ci` reads them with `UNICON_WOODPECKER_TOKEN`
from `.env`, on the CI the dev stack publishes at 127.0.0.1:8000.
`UNICON_E2E_CI_URL` names another.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from bootstrap import envfile
from tests.e2e.stack import DEPLOY

CI = os.environ.get("UNICON_E2E_CI_URL", "http://127.0.0.1:8000").rstrip("/")
GRADING_VARIABLE = "UNICON_GRADING_ID"


def client() -> httpx.Client:
    token = envfile.load(DEPLOY / ".env")["UNICON_WOODPECKER_TOKEN"]
    return httpx.Client(
        base_url=CI, headers={"Authorization": f"Bearer {token}"}, timeout=30
    )


def _all(ci: httpx.Client, path: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    page = 1
    while True:
        answered = ci.get(path, params={"page": page, "per_page": 50})
        answered.raise_for_status()
        items = answered.json() or []
        if not items:
            return found
        found += items
        page += 1


def pipelines_of(ci: httpx.Client, grading: str) -> list[dict[str, Any]]:
    """Every pipeline whose variables name `grading`, oldest first, each
    with its workflows and their steps. A restart keeps the variables of
    the run it restarts, so a restarted attempt's pipeline is among them.
    """
    found = []
    for repo in _all(ci, "/api/repos"):
        for listed in _all(ci, f"/api/repos/{repo['id']}/pipelines"):
            if (listed.get("variables") or {}).get(GRADING_VARIABLE) == grading:
                detail = ci.get(f"/api/repos/{repo['id']}/pipelines/{listed['number']}")
                detail.raise_for_status()
                found.append({**detail.json(), "repo_id": repo["id"]})
    return sorted(found, key=lambda pipeline: int(pipeline["number"]))


def steps_of(pipeline: dict[str, Any]) -> dict[str, list[str]]:
    """Each workflow's steps by name, in order."""
    return {
        workflow["name"]: [step["name"] for step in workflow.get("children") or []]
        for workflow in pipeline.get("workflows") or []
    }


def log_of(ci: httpx.Client, pipeline: dict[str, Any], step: str) -> str:
    """What step `step` of `pipeline` printed."""
    [found] = [
        each
        for workflow in pipeline.get("workflows") or []
        for each in workflow.get("children") or []
        if each["name"] == step
    ]
    answered = ci.get(
        f"/api/repos/{pipeline['repo_id']}/logs/{pipeline['number']}/{found['id']}"
    )
    answered.raise_for_status()
    return "".join(
        base64.b64decode(entry["data"]).decode(errors="replace")
        for entry in answered.json() or []
    )
