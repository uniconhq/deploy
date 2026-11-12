n, k = map(int, input().split())
numbers = list(map(int, input().split()))
place = {x: i for i, x in enumerate(numbers)}
for i, x in enumerate(numbers):
    need = k - x
    if need != x and need in place:
        print(i + 1, place[need] + 1)
        break
