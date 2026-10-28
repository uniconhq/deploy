"""Guess the Number's judge: answer up to 20 guesses at the secret in
`input`, and report how many it took."""

import os
import sys
from pathlib import Path

report = os.environ["UNICON_REPORT"]
secret = int(Path("input").read_text())


def write(name: str, value: int) -> None:
    with open(os.path.join(report, name), "w") as stream:
        stream.write(f"{value}\n")


for guesses in range(1, 21):
    line = sys.stdin.readline()
    if not line.strip():
        write("verdict", 1)  # the entrant left
        break
    guess = int(line)
    write("guesses", guesses)
    if guess == secret:
        write("verdict", 0)
        print("correct", flush=True)  # may fail if the entrant has gone
        break
    try:
        print("higher" if guess < secret else "lower", flush=True)
    except BrokenPipeError:
        write("verdict", 1)  # the entrant left before it was answered
        break
else:
    write("verdict", 1)
