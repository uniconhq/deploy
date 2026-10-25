"""The diff script of unicon/classic@v3: does a run's output match the answer?

exec runs it with the run's `output` and the test's `answer` placed in its
working directory, and `program.yaml` beside this file as its manifest. It
reports through the folder exec names in `UNICON_REPORT`: `verdict` 0 and
`score` 1 when the two match, and `verdict` 1 when they do not, so a wrong
answer's score counts as 0, the worst its bounds allow.

Two files match when they have the same lines once trailing whitespace
(spaces, tabs, carriage returns, vertical tabs and form feeds) is removed
from every line and trailing blank lines are removed from the end. Leading
whitespace and blank lines between other lines still count. The files are
compared as bytes, in blocks, so no text encoding is assumed and the time is
linear in their size. The memory is a few blocks for most files, with two
exceptions. A run of whitespace is held until what follows it is read, so
an output of nothing but spaces is held whole, up to the 32 MB exec keeps
of one, and so is a run in the answer. A block whose lines still end in
whitespace after `TRIM_PASSES` passes is split into lines, which for short
lines takes about 40 times the block. Together they were measured at
74 MB, so the step needs a memory limit of 128 MB or more. This is the rule of unicon/diff-check@v2, which this
script replaces.

A file that is missing or cannot be read ends the script with an error, as a
program that cannot judge does: exec makes that a fault for staff, never a
score.
"""

import os
import sys
from collections.abc import Iterator
from itertools import chain
from pathlib import Path
from typing import BinaryIO

BLOCK = 1 << 20
LINE_SPACE = b" \t\r\x0b\x0c"
"""The whitespace `bytes.rstrip` takes off the end of a line, but the newline."""
LINE_ENDS = tuple(bytes((space,)) + b"\n" for space in LINE_SPACE)
TRIM_PASSES = 4


def main() -> int:
    """Compare `output` with `answer` and write the report."""
    report = Path(os.environ["UNICON_REPORT"])
    if same(Path("output"), Path("answer")):
        (report / "score").write_text("1\n")
        (report / "verdict").write_text("0\n")
    else:
        (report / "verdict").write_text("1\n")
    return 0


def same(actual: Path, expected: Path) -> bool:
    """Whether two files match, ignoring trailing whitespace and trailing blank lines.

    The files are read in blocks of `BLOCK` bytes. Blocks that are the same
    byte for byte are passed over, so two identical files are compared at the
    speed of reading them. From the first blocks that differ, each file's
    lines have their trailing whitespace taken out (see `trimmed`) and the two
    trimmed streams are compared. Leaving out the same bytes from the start of
    both does not change the answer, even when they end inside a line: what is
    left of that line in each is compared, trimmed, as the whole line would
    be. Once one stream runs out, all that is left of the other must be
    newlines, which is what ignoring trailing blank lines means.
    """
    with actual.open("rb") as left, expected.open("rb") as right:
        while (a := left.read(BLOCK)) == (b := right.read(BLOCK)):
            if not a:
                return True
        ours, theirs = trimmed(a, left), trimmed(b, right)
        a = b = b""
        while True:
            a = a or next(ours, b"")
            b = b or next(theirs, b"")
            if not a or not b:
                break
            length = min(len(a), len(b))
            if a[:length] != b[:length]:
                return False
            a, b = a[length:], b[length:]
        rest, more = (a, ours) if a else (b, theirs)
        return not rest.strip(b"\n") and not any(block.strip(b"\n") for block in more)


def trimmed(first: bytes, stream: BinaryIO) -> Iterator[bytes]:
    """`first` and the rest of the stream with each line's trailing whitespace
    taken out, in non-empty blocks.

    The whitespace at the end of what has been read so far is held back, in
    the blocks it was read in, until the next byte that is not whitespace
    shows whether it ends a line: a newline drops it, anything else lets it
    through. At the end of the stream it is dropped. A long run of
    whitespace is held whole, however many blocks it takes.
    """
    held: list[bytes] = []
    start = (first,) if first else ()
    for block in chain(start, iter(lambda: stream.read(BLOCK), b"")):
        kept = block.rstrip(LINE_SPACE)
        if not kept:
            held.append(block)
            continue
        if held and not block.lstrip(LINE_SPACE).startswith(b"\n"):
            yield from held
        held = [block[len(kept) :]] if len(kept) < len(block) else []
        yield trim_lines(kept)


def trim_lines(data: bytes) -> bytes:
    """`data` with the whitespace before each of its newlines taken out.

    Each pass takes one whitespace byte from before every newline, searching
    and replacing at the speed of the bytes methods, so the usual one or two
    (a space, a carriage return) go in a pass or two. Whitespace still left
    after `TRIM_PASSES` passes is on lines longer than that, so there are few
    of them, and the data is then split into lines and each one trimmed.
    """
    for _ in range(TRIM_PASSES):
        ends = [end for end in LINE_ENDS if end in data]
        if not ends:
            return data
        for end in ends:
            data = data.replace(end, b"\n")
    return b"\n".join([line.rstrip(LINE_SPACE) for line in data.split(b"\n")])


if __name__ == "__main__":
    sys.exit(main())
