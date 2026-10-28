import sys

sys.setrecursionlimit(10000)
data = sys.stdin.read().split()
rows, cols = int(data[0]), int(data[1])
grid = [list(row) for row in data[2 : 2 + rows]]


def sink(y, x):
    if 0 <= y < rows and 0 <= x < cols and grid[y][x] == "#":
        grid[y][x] = "."
        sink(y + 1, x)
        sink(y - 1, x)
        sink(y, x + 1)
        sink(y, x - 1)


islands = 0
for r in range(rows):
    for c in range(cols):
        if grid[r][c] == "#":
            islands += 1
            sink(r, c)
print(islands)
