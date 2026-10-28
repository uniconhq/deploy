# Making Change

The till holds coins worth 1, 7, 10 and 25, as many of each as you like.
Pay an amount with as few coins as you can.

Taking the largest coin that fits does not always work: 14 is two 7s,
not a 10 and four 1s.

## Input

One line with the amount *n*, between 1 and 1,000,000.

## Output

The coins, separated by spaces, in any order.

## Scoring

Each test scores 1 for the fewest coins, and half for coins that add up
to *n* but are more than needed. Your points are the average over the six
tests, out of 100.

Submissions after the due time are accepted and marked late.

## Example

Input:

    14

Output:

    7 7
