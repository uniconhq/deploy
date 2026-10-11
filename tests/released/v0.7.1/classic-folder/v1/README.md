# classic-folder

The classic workflow over a program of several files. Compiles the
contestant's folder of sources once, starting from the entry point they
name, runs the program on every test under the task's time and memory
limits, and compares each run's output with that test's answer. A task that
names `unicon/classic-folder@v1` as its workflow is graded that way.

The contestant gives a folder of sources, which keeps its layout, so a Java
package tree compiles as they laid it out; its language, one of `c`, `cpp`,
`java` and `python`, which the task may narrow; and `entry`, the file to
start from, which the task may default. The task gives `time_limit`, in
seconds, and `memory_limit`, in megabytes. Each test is a folder under
`tests/<group>/<test>/` holding `input` and `answer`.

A submission that does not compile stops the run, and every test is
`skipped`. Otherwise each test's outcome is its run's, when the program went
over a limit or crashed, or else the check's: `accepted` when the output
matches the answer, `wrong_answer` when it does not.

Each test reports `time_ms` and `memory_kb`, which fold over tests by their
largest, lower being better, and the compiler's output is reported once as
`log`.
