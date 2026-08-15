from __future__ import annotations

from typing import Any

from scoresight.core.models import NormalizedRect, Point, PreprocessConfig


def crop_region(image: Any, bounds: tuple[int, int, int, int]) -> Any:
    x1, y1, x2, y2 = bounds
    return image[y1:y2, x1:x2]


def transform_frame(
    image: Any,
    *,
    crop: NormalizedRect | None = None,
    perspective: list[Point] | None = None,
) -> Any:
    import cv2
    import numpy as np

    transformed = image
    height, width = transformed.shape[:2]
    if perspective is not None:
        source = np.asarray(
            [[point.x * width, point.y * height] for point in perspective],
            dtype=np.float32,
        )
        destination = np.asarray(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(source, destination)
        transformed = cv2.warpPerspective(transformed, matrix, (width, height))
    if crop is not None:
        transformed = crop_region(transformed, crop.pixels(width, height))
    return transformed


def preprocess(image: Any, config: PreprocessConfig) -> Any:
    """Apply the scoreboard preprocessing subset without importing OpenCV at startup."""

    import cv2
    import numpy as np

    patch = image
    if len(patch.shape) == 3:
        patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    if config.threshold_method == "otsu":
        _, patch = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    elif config.threshold_method == "adaptive":
        patch = cv2.adaptiveThreshold(
            patch, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
    if config.invert:
        patch = 255 - patch
    if config.dilate_iterations:
        patch = cv2.dilate(
            patch,
            np.ones((3, 3), dtype=np.uint8),
            iterations=config.dilate_iterations,
        )
    if config.vertical_scale != 1.0:
        patch = cv2.resize(
            patch,
            None,
            fx=1.0,
            fy=config.vertical_scale,
            interpolation=cv2.INTER_AREA,
        )
    return patch
