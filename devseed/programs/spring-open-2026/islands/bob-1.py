import sys
from collections import deque

data = sys.stdin.read().split()
rows, cols = int(data[0]), int(data[1])
grid = data[2 : 2 + rows]
seen = [[False] * cols for _ in range(rows)]
islands = 0
for r in range(rows):
    for c in range(cols):
        if grid[r][c] != "#" or seen[r][c]:
            continue
        islands += 1
        seen[r][c] = True
        todo = deque([(r, c)])
        while todo:
            y, x = todo.popleft()
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < rows and 0 <= nx < cols and grid[ny][nx] == "#" and not seen[ny][nx]:
                    seen[ny][nx] = True
                    todo.append((ny, nx))
print(islands)
