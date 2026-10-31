import sys

data = sys.stdin.read().split()
n, k = int(data[0]), int(data[1])
numbers = sorted((int(x), i) for i, x in enumerate(data[2 : 2 + n], 1))
lo, hi = 0, n - 1
best = (abs(numbers[lo][0] + numbers[hi][0] - k), lo, hi)
while lo < hi:
    total = numbers[lo][0] + numbers[hi][0]
    best = min(best, (abs(total - k), lo, hi))
    if total < k:
        lo += 1
    elif total > k:
        hi -= 1
    else:
        break
print(numbers[best[1]][1], numbers[best[2]][1])
