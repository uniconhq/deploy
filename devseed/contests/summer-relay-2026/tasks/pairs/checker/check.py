"""Closest Pair's checker, for judgekit/check@v1: are the two places
different, and how close is their sum to k? `answer` holds b, how far the
closest pair's sum is from k; a pair d away scores (b + 1) / (d + 1)."""

import os
from fractions import Fraction
from pathlib import Path

report = Path(os.environ["UNICON_REPORT"])
lines = Path("input").read_text().split("\n")
n, k = map(int, lines[0].split())
numbers = list(map(int, lines[1].split()))
best = int(Path("answer").read_text())
words = Path("output").read_text().split()


def verdict(value: int, message: str, score: Fraction | None = None) -> None:
    (report / "verdict").write_text(f"{value}\n")
    (report / "message").write_text(f"{message}\n")
    if score is not None:
        (report / "score").write_text(f"{float(score)}\n")


if len(words) != 2 or not all(word.isdigit() for word in words):
    verdict(1, "Print two places.")
else:
    i, j = int(words[0]), int(words[1])
    if not (1 <= i <= n and 1 <= j <= n) or i == j:
        verdict(1, "The places must be two different ones from 1 to n.")
    else:
        total = numbers[i - 1] + numbers[j - 1]
        off = abs(total - k)
        if off <= best:
            verdict(
                0,
                f"{numbers[i - 1]} + {numbers[j - 1]} = {total}: as close as it gets.",
                Fraction(1),
            )
        else:
            verdict(
                0,
                f"{numbers[i - 1]} + {numbers[j - 1]} = {total}, {off} from {k}; "
                f"the closest pair is {best} away.",
                Fraction(best + 1, off + 1),
            )
