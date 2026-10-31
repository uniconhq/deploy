# Closest Pair

Given *n* numbers and a target *k*, find two of them, at different
places, whose sum is as close to *k* as possible.

## Input

A line with *n* and *k*, then a line with the *n* numbers. *n* is from 2
to 20,002; every number and *k* are between 1 and 2,000,000,000.

## Output

The places of the two numbers, counting from 1, separated by a space.
When several pairs are equally close, print any of them.

## Scoring

Each test is worth 20 points. A pair as close as possible scores them
all. A pair whose sum is *d* away from *k*, when the closest pair is *b*
away, scores 20 × (*b* + 1) / (*d* + 1). An output that is not two
different places scores nothing.

## Example

Input:

    4 9
    3 8 1 5

Output:

    2 3

8 + 1 is exactly 9. Printing `3 2` is just as right.
