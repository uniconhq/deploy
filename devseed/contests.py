"""The seed's three contests' settings, `contest.yaml`, at the times the
seed runs them: Spring Open 2026, which ran this morning and has closed;
Summer Relay 2026, running for two weeks, in teams of up to two; and Autumn
Open 2026, a draft two months out.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Any

from ruamel.yaml import YAML

WHEN = "%Y-%m-%dT%H:%M:%SZ"
SPRING = "spring-open-2026"
RELAY = "summer-relay-2026"
AUTUMN = "autumn-open-2026"
SPRING_TASKS = ("sum-of-two", "islands", "making-change", "guess-the-number")
RELAY_TASKS = ("pairs", "round-trip", "array-sum", "java-sum")
AUTUMN_TASKS = ("warm-up",)
LATE_TASK = "making-change"
"""Spring's one task with a due time, set once its first submission is in."""
SHORT = {
    "sum-of-two": "sum",
    "islands": "islands",
    "making-change": "change",
    "guess-the-number": "guess",
    "pairs": "pairs",
    "round-trip": "trip",
    "array-sum": "arrays",
    "java-sum": "java",
}
"""Each task's name in its leaderboard columns, which have no titles."""


def spring(
    state: str, start: datetime, end: datetime, due: datetime | None = None
) -> str:
    return dump(
        {
            "name": "Spring Open 2026",
            "description": (
                "Riverside's spring individual contest: four problems in five "
                "hours, from warm-up to interactive."
            ),
            "start": at(start),
            "end": at(end),
            "state": state,
            "visibility": "everyone",
            "tasks": [
                {"id": task, "due": at(due)}
                if task == LATE_TASK and due is not None
                else {"id": task}
                for task in SPRING_TASKS
            ],
            "leaderboards": {
                "standings": {
                    "title": "Standings",
                    "audience": {"who": "everyone"},
                    **icpc(SPRING_TASKS),
                },
                "points": {
                    "title": "Points",
                    "audience": {"who": "entrants", "rows": "all"},
                    **points(
                        {
                            "sum_points": hundred_if_solved("sum-of-two"),
                            "islands_points": {
                                "block": "otherwise",
                                "of": [
                                    {
                                        "block": "best_each",
                                        "from": {"results": "islands"},
                                        "value": {"name": "points"},
                                        "directions": {
                                            "small_ok": "desc",
                                            "large_ok": "desc",
                                        },
                                    },
                                    0,
                                ],
                            },
                            "change_points": best("making-change", "points"),
                            "guess_points": hundred_if_solved("guess-the-number"),
                        }
                    ),
                },
            },
        }
    )


def relay(state: str, start: datetime, end: datetime) -> str:
    return dump(
        {
            "name": "Summer Relay 2026",
            "description": (
                "Two weeks, three problems, teams of up to two. Any member may "
                "submit for the team."
            ),
            "start": at(start),
            "end": at(end),
            "state": state,
            "visibility": "signed-in",
            "entrants": {"most": 2, "locks": "first_submission"},
            "tasks": [{"id": task} for task in RELAY_TASKS],
            "leaderboards": {
                "relay": {
                    "title": "Relay points",
                    "audience": {"who": "everyone"},
                    **points(
                        {
                            "pairs_points": best("pairs", "points"),
                            "trip_points": best("round-trip", "points"),
                            "arrays_points": best("array-sum", "points"),
                        }
                    ),
                },
            },
        }
    )


def autumn(start: datetime) -> str:
    return dump(
        {
            "name": "Autumn Open 2026",
            "description": "Riverside's autumn individual contest. Problems to come.",
            "start": at(start),
            "end": at(start + timedelta(hours=5)),
            "state": "draft",
            "visibility": "everyone",
            "tasks": [{"id": task} for task in AUTUMN_TASKS],
        }
    )


def best(task: str, value: str) -> dict[str, Any]:
    """The highest of `value` over the task's submissions, 0 with none."""
    pick = {
        "block": "highest",
        "from": {"results": task},
        "by": {"value": [task, value]},
    }
    return {"block": "otherwise", "of": [{"value": [pick, value]}, 0]}


def hundred_if_solved(task: str) -> dict[str, Any]:
    return {"block": "mul", "of": [100, best(task, "solved")]}


def points(columns: dict[str, Any]) -> dict[str, Any]:
    """A leaderboard of each task's points and their total, highest first."""
    total = {"block": "add", "of": [{"column": name} for name in columns]}
    return {
        "columns": {**columns, "total": total},
        "sort": [{"total": "desc"}],
        "display": [*columns, {"total": {"decimals": 0}}],
    }


def icpc(tasks: tuple[str, ...]) -> dict[str, Any]:
    """ICPC's standings: problems solved, then the penalty, a solve's minute
    from the start and 20 for each try before it that compiled."""
    columns: dict[str, Any] = {}
    for task in tasks:
        name = SHORT[task]
        win, tries, minute = f"{name}_win", f"{name}_tries", f"{name}_minute"
        columns[win] = {
            "block": "first",
            "from": {"results": task},
            "where": {"block": "eq", "of": [{"value": [task, "solved"]}, 1]},
        }
        columns[tries] = {
            "block": "count",
            "over": {"results": task},
            "where": {
                "block": "and",
                "of": [
                    {"block": "eq", "of": [{"value": [task, "compiled"]}, 1]},
                    {"block": "eq", "of": [{"value": [task, "solved"]}, 0]},
                    {
                        "block": "otherwise",
                        "of": [
                            {
                                "block": "lt",
                                "of": [
                                    {"value": [task, "attempt"]},
                                    {"value": [win, "attempt"]},
                                ],
                            },
                            1,
                        ],
                    },
                ],
            },
        }
        columns[minute] = {
            "block": "if",
            "test": {"column": win},
            "then": {
                "block": "floor",
                "of": {"block": "div", "of": [{"value": [win, "seconds"]}, 60]},
            },
            "else": 0,
        }
        columns[f"{name}_time"] = {
            "block": "if",
            "test": {"column": win},
            "then": {
                "block": "add",
                "of": [
                    {"column": minute},
                    {"block": "mul", "of": [20, {"column": tries}]},
                ],
            },
            "else": 0,
        }
    names = [SHORT[task] for task in tasks]
    columns["solved"] = {
        "block": "add",
        "of": [
            {"block": "if", "test": {"column": f"{n}_win"}, "then": 1, "else": 0}
            for n in names
        ],
    }
    columns["penalty"] = {
        "block": "add",
        "of": [{"column": f"{n}_time"} for n in names],
    }
    return {
        "columns": columns,
        "sort": [{"solved": "desc"}, {"penalty": "asc"}],
        "display": [
            "solved",
            "penalty",
            *(column for n in names for column in (f"{n}_tries", f"{n}_time")),
        ],
    }


def at(moment: datetime) -> str:
    return moment.strftime(WHEN)


def dump(settings: dict[str, Any]) -> str:
    yaml = YAML()
    yaml.default_flow_style = None
    yaml.width = 88
    text = io.StringIO()
    yaml.dump(settings, text)
    return text.getvalue()
