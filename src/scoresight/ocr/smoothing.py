from __future__ import annotations

from collections import Counter, deque


class CharacterSmoother:
    def __init__(self, max_history: int = 5) -> None:
        if max_history < 1:
            raise ValueError("max_history must be positive")
        self.history: deque[str] = deque(maxlen=max_history)

    def add(self, value: str) -> str:
        self.history.append(value)
        output: list[str] = []
        width = max((len(item) for item in self.history), default=0)
        for index in range(width):
            values = [item[index] for item in self.history if index < len(item)]
            if values:
                counts = Counter(values)
                # Prefer the newest value when frequencies tie.
                output.append(max(reversed(values), key=lambda item: counts[item]))
        return "".join(output)

    def clear(self) -> None:
        self.history.clear()
