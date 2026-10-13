# classic

Compiles the submission, runs the program on every test under the task's time
and memory limits, and compares each run's output with that test's answer. A
task that names `unicon/classic@v2` as its workflow is graded that way.

The contestant gives one source file and its language, one of `c`, `cpp`,
`java` and `python`; the task may narrow the languages. The task gives
`time_limit`, in seconds, and `memory_limit`, in megabytes. Each test is a
folder under `tests/<group>/<test>/` holding `input` and `answer`.

A submission that does not compile stops the run, and every test is
`skipped`. Otherwise each test's outcome is its run's, when the program went
over a limit or crashed, or else the check's: `accepted` when the output
matches the answer, `wrong_answer` when it does not.

Each test reports `time_ms`, the program's CPU time, at least the time limit
when it ran out of time, and `memory_kb`, its peak memory. Both fold over
tests by their largest, and lower is better, so a board can rank on a task's
slowest test. The compiler's output is reported once as `log`, so a submission
that does not compile says why.
