from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2

from scoresight.ocr.tesseract_engine import TesseractEngine


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark exact-field OCR accuracy")
    parser.add_argument("manifest", type=Path, help="JSONL with image and expected keys")
    parser.add_argument("--tessdata", type=Path, required=True)
    parser.add_argument("--model", default="scoreboard_general")
    parser.add_argument("--min-accuracy", type=float, default=0.995)
    parser.add_argument("--min-hz", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    records = [
        json.loads(line)
        for line in args.manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    engine = TesseractEngine(args.model, args.tessdata)
    correct = 0
    started = time.perf_counter()
    try:
        for index, record in enumerate(records):
            path = args.manifest.parent / record["image"]
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise FileNotFoundError(path)
            result = engine.recognize(image, region_id=str(index))
            correct += result.text == record["expected"]
    finally:
        engine.close()
    elapsed = time.perf_counter() - started
    accuracy = correct / len(records) if records else 0.0
    frequency = len(records) / elapsed if elapsed else 0.0
    print(json.dumps({"samples": len(records), "accuracy": accuracy, "hz": frequency}, indent=2))
    return 0 if accuracy >= args.min_accuracy and frequency >= args.min_hz else 1


if __name__ == "__main__":
    raise SystemExit(main())
