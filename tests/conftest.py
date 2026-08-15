from __future__ import annotations

import sys
import types
from pathlib import Path

LEGACY_SRC = Path(__file__).resolve().parents[1] / "src"
if str(LEGACY_SRC) not in sys.path:
    sys.path.insert(0, str(LEGACY_SRC))


try:
    import PySide6  # noqa: F401
except ImportError:
    pyside = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")

    class QRectF:
        def __init__(self, x=0, y=0, width=0, height=0):
            self.setRect(x, y, width, height)

        def setRect(self, x, y, width, height):
            self._x, self._y, self._width, self._height = x, y, width, height

        def x(self):
            return self._x

        def y(self):
            return self._y

        def width(self):
            return self._width

        def height(self):
            return self._height

    class QObject:
        pass

    class Signal:
        def __init__(self, *args, **kwargs):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def emit(self, *args):
            for callback in self.callbacks:
                callback(*args)

    qtcore.QRectF = QRectF
    qtcore.QObject = QObject
    qtcore.Signal = Signal
    pyside.QtCore = qtcore
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qtcore
