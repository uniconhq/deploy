"""Making Change's checker: are the coins printed worth the amount, and as
few as can be?

Coins are 1, 7, 10 and 25. `answer` holds the fewest coins for every
amount from 0 to 1,000,000, two bytes each, little-endian. Score 1 for the
fewest, 0.5 for coins that add up but are more than needed, and a wrong
answer otherwise. Each run says why, in its message.
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
    if not coins or any(coin not in COINS for coin in coins):
        verdict(report, 1, "Every coin must be 1, 7, 10 or 25.")
    elif sum(coins) != amount:
        verdict(report, 1, f"The coins add up to {sum(coins)}, not {amount}.")
    elif len(coins) == fewest:
        verdict(report, 0, f"{fewest} coins, the fewest.", score="1")
    else:
        verdict(report, 0, f"{len(coins)} coins; {fewest} would do.", score="0.5")


def verdict(report: Path, value: int, message: str, score: str | None = None) -> None:
    (report / "verdict").write_text(f"{value}\n")
    (report / "message").write_text(f"{message}\n")
    if score is not None:
        (report / "score").write_text(f"{score}\n")


if __name__ == "__main__":
    main()
