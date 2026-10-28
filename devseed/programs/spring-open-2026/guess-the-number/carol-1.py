low, high = 1, 1_000_000
while True:
    guess = (low + high) // 2
    print(guess, flush=True)
    reply = input().strip()
    if reply == "correct":
        break
    if reply == "higher":
        low = guess + 1
    else:
        high = guess - 1
