from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import SimpleQueue
from threading import Lock
from typing import Any

from scoresight.ocr.base import Recognition


class TesseractEngine:
    """Lazy, reusable tesserocr engine instance."""

    def __init__(
        self,
        model: str,
        tessdata_path: Path | None = None,
        character_whitelists: dict[str, str] | None = None,
    ) -> None:
        from tesserocr import PSM, PyTessBaseAPI

        kwargs: dict[str, Any] = {"lang": model, "psm": PSM.SINGLE_WORD}
        if tessdata_path is not None:
            kwargs["path"] = str(tessdata_path)
        self._api = PyTessBaseAPI(**kwargs)
        self._api.SetVariable("load_system_dawg", "F")
        self._api.SetVariable("load_freq_dawg", "F")
        self._character_whitelists = character_whitelists or {}
        self._lock = Lock()

    def recognize(self, image: Any, *, region_id: str) -> Recognition:
        from PIL import Image

        pil_image = image if isinstance(image, Image.Image) else Image.fromarray(image)
        with self._lock:
            self._api.SetVariable(
                "tessedit_char_whitelist",
                self._character_whitelists.get(region_id, ""),
            )
            self._api.SetImage(pil_image)
            text = self._api.GetUTF8Text().strip()
            confidence = max(0.0, min(1.0, self._api.MeanTextConf() / 100.0))
        return Recognition(text=text, confidence=confidence)

    def close(self) -> None:
        self._api.End()


class PooledTesseractEngine:
    """A bounded set of independent Tesseract APIs for parallel OCR regions."""

    def __init__(
        self,
        model: str,
        workers: int,
        tessdata_path: Path | None = None,
        character_whitelists: dict[str, str] | None = None,
    ) -> None:
        if workers < 2:
            raise ValueError("pooled engine requires at least two workers")
        self._engines = [
            TesseractEngine(model, tessdata_path, character_whitelists)
            for _ in range(workers)
        ]
        self._available: SimpleQueue[TesseractEngine] = SimpleQueue()
        for engine in self._engines:
            self._available.put(engine)
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="scoresight-ocr"
        )

    def recognize(self, image: Any, *, region_id: str) -> Recognition:
        engine = self._available.get()
        try:
            return engine.recognize(image, region_id=region_id)
        finally:
            self._available.put(engine)

    def recognize_many(self, requests: list[tuple[Any, str]]) -> list[Recognition]:
        futures = [
            self._executor.submit(self.recognize, image, region_id=region_id)
            for image, region_id in requests
        ]
        return [future.result() for future in futures]

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
        for engine in self._engines:
            engine.close()
