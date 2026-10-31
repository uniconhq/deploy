"""The verdict script unicon/interactive@v2 ships,
workflows/interactive/v2/verdict/: every rule on hand-written facts, as
exec's programs form gives them, and the report it writes for exec's one
form.

Its run through both forms, chained on exec's image, is in
primitive-exec's image tests, which read this folder from a deploy
checkout beside them.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

WORKFLOW = Path(__file__).resolve().parent.parent / "workflows/interactive/v2"
VERDICT = WORKFLOW / "verdict"
SIBLINGS = Path(__file__).resolve().parents[2]
"""Where the primitives' checkouts sit beside deploy's."""


def load_verdict() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verdict", VERDICT / "verdict.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verdict = load_verdict()


def ran(ended: str = "finished", at: int = 100, **more: Any) -> dict[str, Any]:
    """One program's facts, as exec's programs form writes them."""
    return {"time_ms": 10, "memory_kb": 4096, "ended": ended, "ended_at": at} | more


STOPPED: dict[str, Any] = {
    "ended": "runtime_error",
    "signal": "SIGKILL",
    "stopped_by": "entrant",
}
PIPE: dict[str, Any] = {"ended": "runtime_error", "signal": "SIGPIPE"}
"""A native judge killed writing to an entrant that has gone."""
RAISED: dict[str, Any] = {"ended": "runtime_error"}
"""A Python or Java judge that wrote to an entrant that has gone: the write
raised, and the judge exited non-zero, with no signal."""

CASES = [
    # Rule 4: the judge's 0.
    ("accepted", ran(), ran(verdict="0"), "0"),
    # Rule 2: the judge's 1 stands, whichever ended first.
    ("wrong", ran(), ran(verdict="1"), "1"),
    ("wrong, entrant crashed after", ran("runtime_error", 200), ran(verdict="1"), "1"),
    ("wrong, entrant over time", ran("time_limit", 200), ran(verdict="1"), "1"),
    ("judge names a word", ran(), ran(verdict="memory_limit"), "memory_limit"),
    # Rule 3: the entrant's limit or crash.
    (
        "entrant crashed",
        ran("runtime_error"),
        ran(verdict="0", at=200),
        "runtime_error",
    ),
    ("entrant over memory", ran("memory_limit"), ran(verdict="0"), "memory_limit"),
    ("entrant over output", ran("output_limit"), ran(verdict="0"), "output_limit"),
    # Rule 1's first exception: stopped for the entrant's limit.
    ("judge stopped", ran("time_limit"), ran(**STOPPED), "time_limit"),
    (
        "judge stopped after a 0",
        ran("memory_limit"),
        ran(**STOPPED, verdict="0"),
        "memory_limit",
    ),
    ("judge stopped after a 1", ran("time_limit"), ran(**STOPPED, verdict="1"), "1"),
    (
        "entrant stopped by its wall clock",
        ran("time_limit", stopped_by="wall_clock"),
        ran(**STOPPED),
        "time_limit",
    ),
    # Rule 1's third exception: the judge was killed by SIGPIPE at or after
    # the entrant's end, with no verdict.
    ("entrant left cleanly", ran(at=100), ran(**PIPE, at=150), "1"),
    (
        "entrant left by crashing",
        ran("runtime_error", 100),
        ran(**PIPE, at=150),
        "runtime_error",
    ),
    ("at the same wake", ran(at=100), ran(**PIPE, at=100), "1"),
    # Rule 1's second exception: the judge ended at or after the entrant
    # with a verdict, read as a finished judge's.
    ("a 1 written before the pipe", ran(at=100), ran(**PIPE, at=150, verdict="1"), "1"),
    ("a 0 written before the pipe", ran(at=100), ran(**PIPE, at=150, verdict="0"), "0"),
    ("a 1 written, then raised", ran(at=100), ran(**RAISED, at=150, verdict="1"), "1"),
    ("a 0 written, then raised", ran(at=100), ran(**RAISED, at=150, verdict="0"), "0"),
    (
        "a 0 written, entrant crashed",
        ran("runtime_error", 100),
        ran(**RAISED, at=150, verdict="0"),
        "runtime_error",
    ),
    (
        "a word written before the pipe",
        ran(at=100),
        ran(**PIPE, at=150, verdict="time_limit"),
        "time_limit",
    ),
    (
        "a 1 written, judge then over its time",
        ran(at=100),
        ran("time_limit", 150, verdict="1"),
        "1",
    ),
]


@pytest.mark.parametrize(
    ("entrant", "judge", "expected"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_the_rules(
    entrant: dict[str, Any], judge: dict[str, Any], expected: str
) -> None:
    assert verdict.decide(entrant, judge) == expected


@pytest.mark.parametrize("written", ["0", "1", None])
@pytest.mark.parametrize("entrant_ended", ["finished", "runtime_error"])
def test_how_a_judge_ended_after_the_entrant_does_not_change_the_verdict(
    written: str | None, entrant_ended: str
) -> None:
    """A judge that writes its verdict and then a farewell line to an entrant
    that has gone finishes, dies of `SIGPIPE`, or raises and exits non-zero,
    by when the entrant's end reached it: the verdict is the same. With no
    verdict, dying of `SIGPIPE` is the entrant's verdict and raising a
    fault."""
    entrant = ran(entrant_ended, 100)
    if written is None:
        assert verdict.decide(entrant, ran(**PIPE, at=150)) in ("1", entrant_ended)
        with pytest.raises(verdict.NotJudged):
            verdict.decide(entrant, ran(**RAISED, at=150))
        return
    report = {"verdict": written}
    endings = [ran(**PIPE, at=150, **report), ran(**RAISED, at=150, **report)]
    endings.append(ran(at=150, **report))
    assert len({verdict.decide(entrant, judge) for judge in endings}) == 1


NOT_JUDGED = [
    ("judge crashed", ran(), ran("runtime_error", 50), "ended in runtime error"),
    ("judge over its time", ran(), ran("time_limit", 50), "ended in time limit"),
    (
        "judge over memory",
        ran(stopped_by="judge"),
        ran("memory_limit", 50),
        "memory limit",
    ),
    (
        "judge crashed after a 0",
        ran(),
        ran("runtime_error", 50, verdict="0"),
        "before the entrant",
    ),
    (
        "judge crashed after a 1",
        ran(),
        ran("runtime_error", 50, verdict="1"),
        "before the entrant",
    ),
    (
        "judge stopped by its wall clock",
        ran(),
        ran("time_limit", stopped_by="wall_clock"),
        "stopped by wall_clock",
    ),
    (
        "the pipe before the entrant ended",
        ran(at=200),
        ran(**PIPE, at=150),
        "ended in runtime error by SIGPIPE before the entrant",
    ),
    (
        "another signal before the entrant ended",
        ran(at=200),
        ran("runtime_error", 150, signal="SIGSEGV"),
        "SIGSEGV",
    ),
    (
        "judge over its own time after the entrant, no verdict",
        ran(at=100),
        ran("time_limit", 150),
        "time limit with no verdict",
    ),
    (
        "judge over its own memory after the entrant, no verdict",
        ran(at=100),
        ran("memory_limit", 150, signal="SIGKILL"),
        "memory limit by SIGKILL with no verdict",
    ),
    (
        "raised after the entrant left cleanly, no verdict",
        ran(at=100),
        ran(**RAISED, at=150),
        "runtime error with no verdict",
    ),
    (
        "raised after the entrant crashed, no verdict",
        ran("runtime_error", 100),
        ran(**RAISED, at=150),
        "runtime error with no verdict",
    ),
    (
        "another signal after the entrant, no verdict",
        ran(at=100),
        ran("runtime_error", 150, signal="SIGSEGV"),
        "SIGSEGV with no verdict",
    ),
    (
        "stopped by another program",
        ran(),
        ran("runtime_error", stopped_by="other"),
        "stopped by other",
    ),
]


@pytest.mark.parametrize(
    ("entrant", "judge", "said"),
    [case[1:] for case in NOT_JUDGED],
    ids=[case[0] for case in NOT_JUDGED],
)
def test_a_judge_that_did_not_judge_is_a_fault(
    entrant: dict[str, Any], judge: dict[str, Any], said: str
) -> None:
    with pytest.raises(verdict.NotJudged, match="the judge did not judge") as raised:
        verdict.decide(entrant, judge)
    assert said in str(raised.value)


def test_a_judge_stopped_while_the_entrant_finished_is_a_fault() -> None:
    with pytest.raises(verdict.NotJudged, match="with no verdict"):
        verdict.decide(ran(), ran(**STOPPED))


def run_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, facts: bytes | None
) -> tuple[int, dict[str, str]]:
    """Run `main` as exec does: `facts` in the working directory, a fresh
    report folder in `UNICON_REPORT`. Returns its exit code and the report."""
    work, report = tmp_path / "run", tmp_path / "report"
    work.mkdir()
    report.mkdir()
    if facts is not None:
        (work / "facts").write_bytes(facts)
    monkeypatch.chdir(work)
    monkeypatch.setenv("UNICON_REPORT", str(report))
    code = verdict.main()
    written = {path.name: path.read_text() for path in report.iterdir()}
    return code, written


def test_the_report_carries_the_judges_texts_and_shows_its_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    judge = ran(
        verdict="0",
        message="found it",
        label="fast",
        staff_message="10 guesses",
        reports={"guesses": 10, "path": "binary"},
        extras={"log": "500 250"},
    )
    facts = {"entrant": ran(), "judge": judge}
    code, written = run_script(tmp_path, monkeypatch, json.dumps(facts).encode())
    assert code == 0
    assert written == {
        "verdict": "0\n",
        "message": "found it",
        "label": "fast",
        "staff_message": "10 guesses",
        "guesses": "10",
        "path": "binary",
        "log": "500 250",
    }


def test_a_word_is_written_as_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = {"entrant": ran("time_limit"), "judge": ran(**STOPPED)}
    code, written = run_script(tmp_path, monkeypatch, json.dumps(facts).encode())
    assert (code, written) == (0, {"verdict": "time_limit\n"})


@pytest.mark.parametrize(
    "facts",
    [
        None,
        b"not json",
        b"[]",
        json.dumps({"entrant": ran()}).encode(),
        json.dumps({"entrant": ran(), "judge": {}}).encode(),
        json.dumps({"entrant": ran(), "judge": ran("runtime_error", 50)}).encode(),
    ],
)
def test_a_fault_exits_non_zero_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    facts: bytes | None,
) -> None:
    code, written = run_script(tmp_path, monkeypatch, facts)
    assert (code, written) == (1, {})
    assert capsys.readouterr().err.startswith("verdict: ")


def test_the_outcome_words_are_the_programs_forms_and_execs() -> None:
    """`WORDS` are the endings the programs form's `ended` names besides
    `finished`, and the verdict words exec takes from a manifest program,
    read from the sibling exec checkout."""
    exec_source = SIBLINGS / "primitive-exec" / "src" / "exec" / "report.py"
    exec_yaml = SIBLINGS / "primitive-exec" / "primitive.yaml"
    if not (exec_source.is_file() and exec_yaml.is_file()):
        pytest.skip("no primitive-exec checkout beside deploy")
    words = next(
        ast.literal_eval(node.value)
        for node in ast.parse(exec_source.read_text()).body
        if isinstance(node, ast.Assign)
        and [getattr(target, "id", None) for target in node.targets]
        == ["VERDICT_WORDS"]
    )
    declared = YAML(typ="safe").load(exec_yaml)
    programs = declared["forms"]["programs"]
    ended = programs["inputs"]["programs"]["outputs"]["ended"]["options"]
    assert tuple(words) == verdict.WORDS
    assert ["finished", *verdict.WORDS] == ended


def test_the_manifest_declares_no_report() -> None:
    assert YAML(typ="safe").load(VERDICT / "program.yaml") == {"reports": {}}


def test_the_workflow_links_an_entrant_and_a_judge_and_hands_on_the_facts() -> None:
    workflow = YAML(typ="safe").load(WORKFLOW / "workflow.yaml")
    assert "test" not in workflow
    steps = {step["id"]: step for step in workflow["steps"]}
    assert list(steps) == ["game", "decide"]
    game = steps["game"]
    assert game["use"] == "unicon/exec@v2"
    programs = game["with"]["programs"]
    assert set(programs) == {"entrant", "judge"}
    assert game["with"]["links"] == ["entrant -> judge", "judge -> entrant"]
    assert programs["entrant"]["source"] == "${{ inputs.code }}"
    assert programs["entrant"]["language"] == "${{ inputs.language }}"
    assert programs["judge"]["source"] == "${{ inputs.judge }}"
    assert programs["judge"]["language"] == "${{ inputs.judge_language }}"
    assert "manifest" not in programs["entrant"]
    assert "manifest" in programs["judge"]
    decide = steps["decide"]
    assert decide["use"] == "unicon/exec@v2"
    assert decide["with"]["source"] == "${{ workflow.verdict/ }}"
    assert decide["with"]["language"] == "python"
    assert decide["with"]["manifest"] == "${{ workflow.verdict/program.yaml }}"
    assert decide["with"]["files"] == {"facts": "${{ steps.game.facts }}"}
    assert "self_reported" not in decide["with"]
    outputs = workflow["outputs"]
    assert outputs["time_ms"]["from"] == "${{ steps.game.entrant.time_ms }}"
    assert outputs["memory_kb"]["from"] == "${{ steps.game.entrant.memory_kb }}"
    assert outputs["log"]["from"] == "${{ steps.game.entrant.log }}"


def test_the_script_has_no_dependency() -> None:
    source = (VERDICT / "verdict.py").read_text()
    imports = {
        line.split()[1]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imports <= {"json", "os", "sys", "pathlib", "typing"}
