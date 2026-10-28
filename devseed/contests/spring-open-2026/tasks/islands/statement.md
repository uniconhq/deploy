# Grid Islands

A map is a grid of land (`#`) and water (`.`). An island is a group of
land cells joined side to side; cells that touch only at a corner are not
joined. How many islands are there?

## Input

A line with the number of rows *r* and columns *c*, each between 1 and
300, then *r* lines of *c* characters each.

## Output

One line with the number of islands.

## Scoring

- **40 points:** the sample and the small and medium grids, up to 40 by
  50.
- **60 points:** two grids of 300 by 300, one of them a single island
  that winds through every row.

Points are shown once the results are released.

## Example

Input:

    4 5
    ##..#
    #...#
    ..#..
    ....#

Output:

    4
