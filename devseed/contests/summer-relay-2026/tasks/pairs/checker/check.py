"""Pairs' checker, for judgekit/check@v1: are the two places different, and
do their numbers add up to k? `answer` says whether any pair does."""

import os
from pathlib import Path

report = Path(os.environ["UNICON_REPORT"])
lines = Path("input").read_text().split("\n")
n, k = map(int, lines[0].split())
numbers = list(map(int, lines[1].split()))
exists = Path("answer").read_text().strip() == "yes"
words = Path("output").read_text().split()


def verdict(value: int, message: str) -> None:
    (report / "verdict").write_text(f"{value}\n")
    (report / "message").write_text(f"{message}\n")
    if value == 0:
        (report / "score").write_text("1\n")


if words == ["-1"]:
    if exists:
        verdict(1, "There is a pair.")
    else:
        verdict(0, "Right: there is no pair.")
elif len(words) != 2 or not all(word.isdigit() for word in words):
    verdict(1, "Print two places or -1.")
else:
    i, j = int(words[0]), int(words[1])
    if not (1 <= i <= n and 1 <= j <= n) or i == j:
        verdict(1, "The places must be two different ones from 1 to n.")
    elif numbers[i - 1] + numbers[j - 1] != k:
        verdict(1, f"{numbers[i - 1]} + {numbers[j - 1]} is not {k}.")
    else:
        verdict(0, "Right.")
