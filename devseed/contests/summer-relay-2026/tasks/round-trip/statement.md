# Round Trip

A word is encoded by moving each letter forward in the alphabet by its
place in the word: the first letter by 1, the second by 2, and so on,
going round from `z` to `a`. So `abc` becomes `bdf`.

Your program is run twice on each test: once to encode, once to decode.
Each of the four tests is worth 25 points when both ways are right.

## Input

One line: `encode` and a word, or `decode` and a code. Words are 1 to 100
lower-case letters.

## Output

One line with the code, or the word.

## Example

Input:

    encode abc

Output:

    bdf

Input:

    decode bdf

Output:

    abc
