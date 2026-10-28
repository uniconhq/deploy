import sys

data = sys.stdin.read().split()
n, k = int(data[0]), int(data[1])
numbers = list(map(int, data[2 : 2 + n]))
place = {x: i for i, x in enumerate(numbers, 1)}
for i, x in enumerate(numbers, 1):
    if k - x in place:
        print(i, place[k - x])
        break
else:
    print(-1)
