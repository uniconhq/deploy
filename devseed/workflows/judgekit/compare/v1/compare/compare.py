"""judgekit/compare@v1: does `output` hold the same words as `answer`?"""

import os
from pathlib import Path

report = Path(os.environ["UNICON_REPORT"])
got = Path("output").read_text(errors="replace").split()
want = Path("answer").read_text(errors="replace").split()


def verdict(value: int, message: str) -> None:
    (report / "verdict").write_text(f"{value}\n")
    (report / "message").write_text(f"{message}\n")
    if value == 0:
        (report / "score").write_text("1\n")


differs = next(
    (at for at, (a, b) in enumerate(zip(got, want, strict=False)) if a != b), None
)
if differs is not None:
    verdict(1, f"Word {differs + 1} is not the answer's.")
elif len(got) < len(want):
    verdict(1, f"Too few words: {len(got)}, where the answer has {len(want)}.")
elif len(got) > len(want):
    verdict(1, f"Too many words: {len(got)}, where the answer has {len(want)}.")
else:
    verdict(0, "Matches the answer.")
