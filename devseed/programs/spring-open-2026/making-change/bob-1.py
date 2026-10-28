n = int(input())
best = None
for quarters in range(n // 25, max(-1, n // 25 - 7), -1):
    rest = n - 25 * quarters
    for tens in range(rest // 10, -1, -1):
        left = rest - 10 * tens
        sevens, ones = divmod(left, 7)
        count = quarters + tens + sevens + ones
        if best is None or count < best[0]:
            best = (count, quarters, tens, sevens, ones)
_, quarters, tens, sevens, ones = best
print(*([25] * quarters + [10] * tens + [7] * sevens + [1] * ones))
