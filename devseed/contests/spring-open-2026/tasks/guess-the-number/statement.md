# Guess the Number

The judge has picked a whole number between 1 and 1,000,000. Find it in
at most 20 guesses.

## Interaction

Print a guess on a line of its own and flush. The judge answers on a line
of its own:

- `higher` when the number is larger than your guess;
- `lower` when it is smaller;
- `correct` when you have found it. Stop then.

A 21st guess, or leaving before `correct`, is a wrong answer.

## Example

    > 500000
    < lower
    > 250000
    < lower
    ...
    > 42
    < correct
