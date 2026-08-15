from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from scoresight.web.app import create_app


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the ScoreSight OCR service")
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=18099)
    result.add_argument("--config", type=Path)
    result.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"]
    )
    return result


def main() -> None:
    args = parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_app(args.config)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
