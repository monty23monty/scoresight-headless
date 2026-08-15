from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scoresight.capture.base import FramePacket
from scoresight.core.models import ServiceConfig
from scoresight.core.runtime import RuntimeController


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Evaluate accepted OCR values against labelled frames"
    )
    result.add_argument("manifest", type=Path, help="JSONL corpus manifest")
    result.add_argument("config", type=Path, help="ScoreSight service configuration")
    result.add_argument("--tessdata", type=Path)
    result.add_argument("--minimum-frames", type=int, default=200)
    result.add_argument("--minimum-precision", type=float, default=0.99)
    result.add_argument("--maximum-false-updates", type=int, default=1)
    result.add_argument("--maximum-p95-ms", type=float, default=750.0)
    return result


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile_value)
    return ordered[index]


def load_manifest(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or "image" not in value or "expected" not in value:
            raise ValueError(f"invalid corpus record on line {line_number}")
        records.append(value)
    return records


def main() -> int:
    args = parser().parse_args()
    if args.tessdata:
        os.environ["SCORESIGHT_TESSDATA"] = str(args.tessdata.resolve())
    config = ServiceConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    records = load_manifest(args.manifest)
    pipeline = RuntimeController._build_pipeline(config)
    accepted = 0
    correct = 0
    false_updates = 0
    latencies: list[float] = []
    try:
        import cv2

        for sequence, record in enumerate(records, 1):
            image_path = (args.manifest.parent / str(record["image"])).resolve()
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"unable to read corpus image: {image_path}")
            height, width = image.shape[:2]
            result = pipeline.process(
                FramePacket(
                    sequence=sequence,
                    image=image,
                    width=width,
                    height=height,
                    captured_at=datetime.now(UTC),
                )
            )
            latencies.append(result.latency_ms)
            expected = {str(key): str(value) for key, value in record["expected"].items()}
            for field in result.fields:
                wanted = expected.get(field.id, expected.get(field.name))
                if wanted is None or not field.value:
                    continue
                accepted += 1
                if field.value == wanted:
                    correct += 1
                elif field.state.value == "ok":
                    false_updates += 1
    finally:
        pipeline.close()

    precision = correct / accepted if accepted else 0.0
    p95_ms = percentile(latencies, 0.95)
    summary = {
        "frames": len(records),
        "accepted_observations": accepted,
        "correct_observations": correct,
        "accepted_precision": precision,
        "false_accepted_updates": false_updates,
        "p95_latency_ms": p95_ms,
    }
    print(json.dumps(summary, indent=2))
    passed = (
        len(records) >= args.minimum_frames
        and precision >= args.minimum_precision
        and false_updates <= args.maximum_false_updates
        and p95_ms <= args.maximum_p95_ms
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
