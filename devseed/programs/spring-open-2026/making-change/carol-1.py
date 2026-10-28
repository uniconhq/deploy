n = int(input())
coins = []
for coin in (25, 10, 7, 1):
    while n >= coin:
        coins.append(coin)
        n -= coin
print(*coins)
