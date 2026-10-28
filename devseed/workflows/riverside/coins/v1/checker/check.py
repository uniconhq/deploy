"""Making Change's checker: are the coins printed worth the amount, and as
few as can be?

Coins are 1, 7, 10 and 25. `answer` holds the fewest coins for every
amount from 0 to 1,000,000, two bytes each, little-endian. Score 1 for the
fewest, 0.5 for coins that add up but are more than needed, and a wrong
answer otherwise.
"""

import os
from pathlib import Path

COINS = {1, 7, 10, 25}


def main() -> None:
    report = Path(os.environ["UNICON_REPORT"])
    amount = int(Path("input").read_text().split()[0])
    table = Path("answer").read_bytes()
    fewest = int.from_bytes(table[2 * amount : 2 * amount + 2], "little")
    try:
        coins = [int(word) for word in Path("output").read_text().split()]
    except ValueError:
        coins = []
    if not coins or any(coin not in COINS for coin in coins) or sum(coins) != amount:
        (report / "verdict").write_text("1\n")
        return
    (report / "verdict").write_text("0\n")
    (report / "score").write_text("1\n" if len(coins) == fewest else "0.5\n")


if __name__ == "__main__":
    main()
