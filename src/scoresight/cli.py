from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import uvicorn

from scoresight.core.deployment import DeploymentSettings
from scoresight.core.logging import configure_logging
from scoresight.web.app import create_app


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the ScoreSight OCR service")
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=18099)
    result.add_argument("--config", type=Path)
    result.add_argument("--data-dir", type=Path)
    result.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"]
    )
    return result


def main() -> None:
    args = parser().parse_args()
    deployment = DeploymentSettings.from_env()
    if args.data_dir is not None:
        deployment = replace(deployment, data_dir=args.data_dir)
    configure_logging(args.log_level, json_logs=deployment.json_logs)
    app = create_app(args.config, deployment=deployment)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=deployment.trusted_proxies,
        access_log=deployment.access_log,
        log_config=None,
    )


if __name__ == "__main__":
    main()
