from __future__ import annotations

from scoresight.ocr import tesseract_engine
from scoresight.ocr.base import Recognition


class FakeTesseract:
    instances = []

    def __init__(self, model, tessdata_path=None) -> None:
        self.model = model
        self.closed = False
        self.instances.append(self)

    def recognize(self, image, *, region_id: str) -> Recognition:
        return Recognition(f"{region_id}:{image}", 1.0)

    def close(self) -> None:
        self.closed = True


def test_tesseract_pool_reuses_bounded_engine_instances(monkeypatch) -> None:
    FakeTesseract.instances.clear()
    monkeypatch.setattr(tesseract_engine, "TesseractEngine", FakeTesseract)
    pool = tesseract_engine.PooledTesseractEngine("model", workers=2)
    results = pool.recognize_many([("one", "a"), ("two", "b"), ("three", "c")])
    pool.close()

    assert [result.text for result in results] == ["a:one", "b:two", "c:three"]
    assert len(FakeTesseract.instances) == 2
    assert all(instance.closed for instance in FakeTesseract.instances)
