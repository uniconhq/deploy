# unicon/interactive@v1

One run of an entrant's program against the task's judge. `workflow.yaml`
compiles both, runs them side by side with `unicon/interact@v2`, the
entrant's output the judge's input and the judge's the entrant's, and then
runs `verdict/verdict.py` with `unicon/exec@v1`, given `verdict/program.yaml`
as its manifest and interact's `facts` as a named file. Its verdict is the
run's.

**The judge** is the task's program, with its `program.yaml` as
`judge_manifest` and the test's input placed in its working directory as
`input`. It writes its verdict to `$UNICON_REPORT/verdict`, as a program
given a manifest does under exec. It should judge the entrant leaving, an
end of input or a write that fails, as a `1`, and write a verdict only once
it has judged. Its time limit should be longer than the entrant's, so the
entrant's wall clock runs out first when the two wait on each other.

**The verdict,** in this order:

1. A judge that did not judge, one that did not finish, is an error of the
   step, for staff. Two cases are not:
   - it was stopped because the entrant broke a limit
     (`stopped_by: entrant`): the entrant's limit;
   - it was killed by `SIGPIPE` after the entrant ended: the entrant's
     crash, or `wrong_answer` if the entrant exited cleanly.
2. The judge's wrong answer stands, whichever program ended first.
3. Otherwise a limit the entrant broke, or its crash.
4. Otherwise the judge's `0`: `accepted`.

The judge's texts are passed on with every verdict, and its reports are
shown with the run as extras, never columns. The run reports the entrant's
time and memory and the compile log.

The rules are tested in `tests/test_interactive_verdict.py` on hand-written
facts, and through interact and exec on their images in
`primitive-interact`'s image tests.
