# judgekit/compare@v1

Judges one output against the task's answer file, word by word. Both are
split into words on spaces, tabs and new lines, so extra spaces, blank
lines and a missing last new line do not matter, but every word must
match exactly, letter case included.

- A match is taken and scores 1.
- Anything else is not taken. Its message says which word was the first
  to differ, or that there are too few or too many words. It never shows
  the answer.

Use it as a box after a step that runs the solution, as `alice/two-sum@v1`
does.
