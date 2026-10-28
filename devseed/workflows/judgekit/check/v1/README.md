# judgekit/check@v1

Judges one output with a checker the task brings. The checker is a
Python folder with `check.py` at its top. It finds the test's `input`,
the entrant's `output` and the task's `answer` in its working directory,
and writes to the folder `$UNICON_REPORT` names:

- `verdict`: `0` when it takes the output, `1` when it does not;
- `score`: from 0 to 1, for an output it takes;
- `message`: optional, a line the entrant sees with the run.

Use it as a box after a step that runs the solution, as
`judgekit/special@v1` does.
