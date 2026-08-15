from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


def _csv(value: str | None) -> tuple[str, ...]:
    return tuple(item.strip() for item in (value or "").split(",") if item.strip())


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DeploymentSettings:
    auth_mode: Literal["token", "cloudflare_access"] = "token"
    data_dir: Path | None = None
    public_url: str | None = None
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    trusted_proxies: str = "127.0.0.1"
    cloudflare_team_domain: str | None = None
    cloudflare_audience: str | None = None
    access_log: bool = True
    json_logs: bool = False
    websocket_limit: int = 8

    @classmethod
    def from_env(cls) -> DeploymentSettings:
        mode = os.getenv("SCORESIGHT_AUTH_MODE", "token").strip().lower()
        if mode not in {"token", "cloudflare_access"}:
            raise ValueError("SCORESIGHT_AUTH_MODE must be token or cloudflare_access")
        data_dir_value = os.getenv("SCORESIGHT_DATA_DIR")
        public_url = os.getenv("SCORESIGHT_PUBLIC_URL") or None
        settings = cls(
            auth_mode=mode,  # type: ignore[arg-type]
            data_dir=Path(data_dir_value) if data_dir_value else None,
            public_url=public_url,
            allowed_hosts=_csv(os.getenv("SCORESIGHT_ALLOWED_HOSTS")),
            allowed_origins=_csv(os.getenv("SCORESIGHT_ALLOWED_ORIGINS")),
            trusted_proxies=os.getenv("SCORESIGHT_TRUSTED_PROXIES", "127.0.0.1"),
            cloudflare_team_domain=os.getenv("SCORESIGHT_CF_TEAM_DOMAIN") or None,
            cloudflare_audience=os.getenv("SCORESIGHT_CF_AUDIENCE") or None,
            access_log=_bool(os.getenv("SCORESIGHT_ACCESS_LOG"), default=mode == "token"),
            json_logs=_bool(os.getenv("SCORESIGHT_JSON_LOGS"), default=mode != "token"),
            websocket_limit=max(1, int(os.getenv("SCORESIGHT_WEBSOCKET_LIMIT", "8"))),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.auth_mode == "cloudflare_access":
            if not self.cloudflare_team_domain or not self.cloudflare_audience:
                raise ValueError(
                    "Cloudflare Access mode requires SCORESIGHT_CF_TEAM_DOMAIN and "
                    "SCORESIGHT_CF_AUDIENCE"
                )
            if not self.cloudflare_team_domain.startswith("https://"):
                raise ValueError("SCORESIGHT_CF_TEAM_DOMAIN must be an https URL")
        if self.public_url:
            parsed = urlparse(self.public_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("SCORESIGHT_PUBLIC_URL must be an absolute http(s) URL")

    @property
    def secure_cookies(self) -> bool:
        return bool(self.public_url and self.public_url.startswith("https://"))

    @property
    def effective_allowed_hosts(self) -> tuple[str, ...]:
        health_hosts = ("127.0.0.1", "localhost")
        if self.allowed_hosts:
            return tuple(dict.fromkeys((*self.allowed_hosts, *health_hosts)))
        if self.public_url:
            hostname = urlparse(self.public_url).hostname
            return tuple(dict.fromkeys((hostname, *health_hosts))) if hostname else health_hosts
        return ()

    @property
    def effective_allowed_origins(self) -> tuple[str, ...]:
        if self.allowed_origins:
            return self.allowed_origins
        return (self.public_url.rstrip("/"),) if self.public_url else ()

    def config_path(self, explicit: Path | None) -> Path | None:
        if explicit is not None:
            return explicit
        return self.data_dir / "config-v1.json" if self.data_dir else None

    def profile_path(self, explicit: Path | None) -> Path | None:
        if explicit is not None:
            return explicit
        return self.data_dir / "profiles" if self.data_dir else None
