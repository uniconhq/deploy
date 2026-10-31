# unicon/interactive@v2

One run of an entrant's program against the task's judge. `workflow.yaml`
compiles both and runs them side by side with `unicon/exec@v2`'s programs
form, the entrant's output the judge's input and the judge's the
entrant's, and then compiles and runs `verdict/verdict.py` with
`unicon/exec@v2`'s one form, given `verdict/program.yaml` as its manifest
and the game's `facts` as a named file. Its verdict is the run's. Each
step compiles its sources once, before any run, from the machine's cache
when it can; the save checks that the judge and `verdict/` compile.

**The judge** is the task's program, with its `program.yaml` as
`judge_manifest` and the test's input placed in its working directory as
`input`. It writes its verdict to `$UNICON_REPORT/verdict`, as a program
given a manifest does under exec. It should judge the entrant leaving, an
end of input or a write that fails, as a `1`, and write a verdict only once
it has judged. Its time limit should be longer than the entrant's, so the
entrant's wall clock runs out first when the two wait on each other.

A judge that writes to an entrant that has gone dies of `SIGPIPE` if it is
native, and in Python or Java the write raises an error instead. Either is
fine once the verdict is written: a judge that ends after the entrant is
judged by the verdict it wrote, however it ended. Before then, a judge
must catch the error and write `1` itself: a judge that ends with no
verdict is an error of the step, unless it was killed by `SIGPIPE`. A
Python judge that writes its verdict first and then says goodbye, and
writes `1` when a write fails before that, needs nothing more:

```python
import os, sys

report = os.environ["UNICON_REPORT"]
secret = int(open("input").read())


def verdict(value):
    with open(os.path.join(report, "verdict"), "w") as stream:
        stream.write(f"{value}\n")


for guesses in range(1, 21):
    line = sys.stdin.readline()
    if not line.strip():
        verdict(1)  # the entrant left
        break
    guess = int(line)
    if guess == secret:
        verdict(0)
        print("correct", flush=True)  # may fail if the entrant has gone
        break
    try:
        print("higher" if guess < secret else "lower", flush=True)
    except BrokenPipeError:
        verdict(1)  # the entrant left before it was answered
        break
else:
    verdict(1)
```

**The verdict,** in this order:

1. A judge that did not judge, one that did not finish, is an error of the
   step, for staff. Two cases are not:
   - it was stopped because the entrant broke a limit
     (`stopped_by: entrant`): the entrant's limit;
   - it ended at or after the entrant, by `SIGPIPE`, an error it raised
     or any other way, and wrote a verdict: the verdict is read by the
     rules below, as if it had finished;
   - it was killed by `SIGPIPE` at or after the entrant's end with no
     verdict: the entrant's crash or limit, or `wrong_answer` if the
     entrant exited cleanly. Any other judge with no verdict is an error
     of the step.
2. The judge's wrong answer stands, whichever program ended first.
3. Otherwise a limit the entrant broke, or its crash.
4. Otherwise the judge's `0`: `accepted`.

Code that does not compile ends every run `compile_error`, and neither
program runs.

The judge's texts are passed on with every verdict, and its reports are
shown with the run as extras, never columns. The run reports the entrant's
time and memory and the entrant's compile log, the same for every run.

The rules are tested in `tests/test_interactive_verdict.py` on hand-written
facts, and through both forms of exec on its image in
`primitive-exec`'s image tests.
