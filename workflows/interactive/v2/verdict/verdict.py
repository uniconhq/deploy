"""The verdict script of unicon/interactive@v2: whose run was it?

exec's programs form runs the entrant and the judge side by side and judges nothing; it
writes how each ended as its `facts` file. exec runs this script with that
file placed in its working directory as `facts`, and `program.yaml` beside
this file as its manifest. It reports through the folder exec names in
`UNICON_REPORT`, in this order:

1. A judge that did not judge, one that did not finish, is a fault: the
   script exits non-zero, which exec makes an error of the step, for staff,
   never a verdict. Two cases are not this:
   - the judge was stopped because the entrant broke a limit
     (`stopped_by: entrant`): the run is the entrant's limit;
   - the judge ended at or after the entrant (`ended_at`) and wrote a
     verdict, however it ended: it may have died writing to an entrant
     that had gone, of `SIGPIPE` as a native program does, or of the
     error Python and Java raise instead. Its verdict is read as if it
     had finished, by the rules below, so a judge that wrote `0` and died
     saying goodbye is judged as one that wrote `0` and exited;
   - the judge was killed by `SIGPIPE` at or after the entrant's end,
     with no verdict: a native judge that wrote to an entrant that had
     gone before it judged. The run is the entrant's crash or limit, or
     a wrong answer if it exited cleanly. Any other judge that ended
     with no verdict, one whose write raised among them, is a fault.
2. The judge's wrong answer stands, whichever program ended first, and so
   does an outcome word the judge named as its verdict.
3. Otherwise a limit the entrant broke, or its crash, is the verdict.
4. Otherwise the judge's `0`.

A judge should judge the entrant leaving: an end of input, or a write that
fails because the entrant has gone, is a verdict of `1` it writes, not a
crash. And a judge writes its verdict once it has judged, never a `1` to
change later: a `1` stands even when the judge is then stopped for the
entrant's limit, or dies writing to an entrant that has gone.

On every verdict the judge's texts, `label`, `message` and `staff_message`,
are passed on, and its reports and extras are shown with the run as extras
of this script's, never columns: which reports a judge has is its task's,
and this script's manifest declares none.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

TEXTS = ("label", "message", "staff_message")
WORDS = ("time_limit", "memory_limit", "output_limit", "runtime_error")
"""How a program ended, other than `finished`, in the programs form's facts: each is
also a verdict this script may write, the step's outcome."""


class NotJudged(Exception):
    """The judge did not judge, or the facts are not the programs form's: a fault of
    the step, never a verdict."""


def main() -> int:
    """Read `facts`, decide and write the report."""
    try:
        facts = read_facts(Path("facts"))
        verdict = decide(facts["entrant"], facts["judge"])
    except NotJudged as fault:
        print(f"verdict: {fault}", file=sys.stderr)
        return 1
    write_report(Path(os.environ["UNICON_REPORT"]), verdict, facts["judge"])
    return 0


def read_facts(path: Path) -> dict[str, dict[str, Any]]:
    """The programs form's facts for one item: an entrant and a judge."""
    try:
        facts = json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise NotJudged(f"the facts could not be read: {error}") from None
    if not isinstance(facts, dict):
        raise NotJudged("the facts are not an object")
    for name in ("entrant", "judge"):
        if not isinstance(facts.get(name), dict) or "ended" not in facts[name]:
            raise NotJudged(f"the facts have no program named {name}")
    return facts


def decide(entrant: dict[str, Any], judge: dict[str, Any]) -> str:
    """The verdict to write: `0`, `1` or one of `WORDS`, by the rules above."""
    verdict = judge.get("verdict")
    if judge["ended"] != "finished" and judge.get("stopped_by") != "entrant":
        if judge.get("ended_at", -1) < entrant.get("ended_at", 0):
            raise NotJudged(
                f"the judge did not judge: it ended in {ended(judge)} before "
                "the entrant"
            )
        if verdict is None:
            if judge.get("signal") != "SIGPIPE" or "stopped_by" in judge:
                raise NotJudged(
                    f"the judge did not judge: it ended in {ended(judge)} with "
                    "no verdict"
                )
            return "1" if entrant["ended"] == "finished" else str(entrant["ended"])
    if verdict is not None and verdict != "0":
        return str(verdict)
    if entrant["ended"] != "finished":
        return str(entrant["ended"])
    if verdict == "0":
        return "0"
    raise NotJudged(
        f"the judge ended in {ended(judge)} with no verdict, and the entrant finished"
    )


def ended(program: dict[str, Any]) -> str:
    """How a program ended, in words, with what stopped it."""
    said = str(program["ended"]).replace("_", " ")
    if "signal" in program:
        said += f" by {program['signal']}"
    if "stopped_by" in program:
        said += f", stopped by {program['stopped_by']}"
    return said


def write_report(report: Path, verdict: str, judge: dict[str, Any]) -> None:
    """Write the verdict, the judge's texts, and its reports and extras as
    files of this script's report, shown as extras. The verdict goes last,
    so a report cut short has none."""
    for name in TEXTS:
        if name in judge:
            (report / name).write_text(str(judge[name]))
    shown: dict[str, Any] = {**judge.get("reports", {}), **judge.get("extras", {})}
    for name, value in shown.items():
        (report / name).write_text(f"{value}")
    (report / "verdict").write_text(f"{verdict}\n")


if __name__ == "__main__":
    sys.exit(main())
