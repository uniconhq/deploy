import sys

data = sys.stdin.read().split()
n, k = int(data[0]), int(data[1])
numbers = list(map(int, data[2 : 2 + n]))
place = {}
for i, x in enumerate(numbers, 1):
    if k - x in place:
        print(place[k - x], i)
        break
    place[x] = i
else:
    print(-1)
