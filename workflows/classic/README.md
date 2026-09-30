# classic

Compiles the submission, runs the binary on every testcase under the task's
time and memory limits, and compares each run's output with that testcase's
answer. A task that names `unicon/classic@v1` as its workflow is graded that
way, with the testcases and the limits supplied by the task.

The result is the first testcase's outcome that was not `accepted`, or
`accepted` when every one was, and `points`, one for each accepted testcase.
Each testcase reports the time and memory its run took. The compiler's output
is the summary, so a submission that does not compile says why.
