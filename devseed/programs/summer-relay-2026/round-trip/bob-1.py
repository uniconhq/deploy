way, word = input().split()
step = 1 if way == "encode" else -1
# Moves each letter by its place, but forgets to go round from z to a.
print("".join(chr(ord(ch) + step * i) for i, ch in enumerate(word, 1)))
