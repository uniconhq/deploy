n, k = map(int, input().split())
numbers = list(map(int, input().split()))
seen = {}
for j, x in enumerate(numbers):
    if k - x in seen:
        print(seen[k - x] + 1, j + 1)
        break
    seen.setdefault(x, j)
