from __future__ import annotations

from collections import Counter, deque


class ClockTracker:
    """Confirm whole readings along a plausible clock trajectory, including tenths.

    Large corrections/resets need at least three consistent observations. A
    running clock need not repeat the same text to confirm a new reading.
    """

    def __init__(self, confirmation_frames: int) -> None:
        self.confirmation_frames = confirmation_frames
        self.accepted: tuple[float, float, float] | None = None
        self.pending: tuple[float, float, float] | None = None
        self.pending_count = 0

    @staticmethod
    def _reading(value: str, timestamp: float) -> tuple[float, float, float]:
        if ":" in value:
            minutes, seconds = value.split(":")
            return int(minutes) * 60 + int(seconds), timestamp, 1.0
        return float(value), timestamp, 0.1

    @staticmethod
    def _consistent(
        previous: tuple[float, float, float], current: tuple[float, float, float]
    ) -> bool:
        value, timestamp, resolution = current
        old_value, old_timestamp, old_resolution = previous
        elapsed = max(0.0, timestamp - old_timestamp)
        # Quantized displays can cross a second boundary between adjacent frames.
        return (
            abs(value - old_value) <= elapsed + max(resolution, old_resolution) + 0.02
        )

    def reset_pending(self) -> None:
        self.pending = None
        self.pending_count = 0

    def clear(self) -> None:
        self.accepted = None
        self.reset_pending()

    def add(self, value: str, timestamp: float, *, unchanged: bool) -> bool:
        current = self._reading(value, timestamp)
        if unchanged:
            self.accepted = current
            self.reset_pending()
            return True
        plausible = self.accepted is None or self._consistent(self.accepted, current)
        if self.pending is not None and self._consistent(self.pending, current):
            self.pending_count += 1
        else:
            self.pending_count = 1
        self.pending = current
        required = (
            self.confirmation_frames if plausible else max(3, self.confirmation_frames)
        )
        if self.pending_count < required:
            return False
        self.accepted = current
        self.reset_pending()
        return True


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
