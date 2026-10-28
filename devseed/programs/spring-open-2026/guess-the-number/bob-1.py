guess = 1
while True:
    print(guess, flush=True)
    if input().strip() == "correct":
        break
    guess += 1
