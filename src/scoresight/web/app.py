from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from scoresight.capture.decklink import DeckLinkCapture, DeckLinkUnavailable
from scoresight.capture.mock import MockCapture
from scoresight.capture.opencv import OpenCVCapture
from scoresight.core.config import ConfigStore, RevisionConflict
from scoresight.core.deployment import DeploymentSettings
from scoresight.core.logging import reset_request_id, set_request_id
from scoresight.core.models import HealthComponent, ServiceConfig
from scoresight.core.profiles import ProfileStore
from scoresight.core.runtime import RuntimeController
from scoresight.core.secrets import redact_mapping, restore_redacted
from scoresight.core.service import ScoreSightService
from scoresight.outputs.manager import OutputManager
from scoresight.web.cloudflare import CloudflareAccessVerifier
from scoresight.web.security import (
    ADMIN_COOKIE,
    CSRF_COOKIE,
    SecurityDependencies,
    new_csrf_token,
)

PACKAGE_DIR = Path(__file__).resolve().parent


def _safe_config(config: ServiceConfig) -> dict[str, Any]:
    payload = redact_mapping(config.model_dump(mode="json"))
    payload["security"] = {
        "allow_lan": config.security.allow_lan,
        "read_token_count": len(config.security.read_tokens),
    }
    return payload


def create_app(
    config_path: Path | None = None,
    profile_path: Path | None = None,
    *,
    start_runtime: bool = True,
    deployment: DeploymentSettings | None = None,
    access_verifier: CloudflareAccessVerifier | None = None,
) -> FastAPI:
    deployment = deployment or DeploymentSettings.from_env()
    deployment.validate()
    config_path = deployment.config_path(config_path)
    profile_path = deployment.profile_path(profile_path)
    store = ConfigStore(config_path)
    profiles = ProfileStore(profile_path)
    service = ScoreSightService(store)
    output_manager = OutputManager(service.results)
    runtime = RuntimeController(service, store)
    if deployment.auth_mode == "cloudflare_access" and access_verifier is None:
        assert deployment.cloudflare_team_domain is not None
        assert deployment.cloudflare_audience is not None
        access_verifier = CloudflareAccessVerifier(
            deployment.cloudflare_team_domain, deployment.cloudflare_audience
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = store.load()
        output_manager.configure(config.outputs)
        await output_manager.start()
        if start_runtime:
            await runtime.start()
        yield
        if start_runtime:
            await runtime.stop()
        await output_manager.stop()
        await service.stop()
        if access_verifier is not None:
            await access_verifier.close()

    app = FastAPI(title="ScoreSight", version="0.2.0", lifespan=lifespan)
    app.state.service = service
    app.state.config_store = store
    app.state.profile_store = profiles
    app.state.output_manager = output_manager
    app.state.runtime = runtime
    app.state.deployment = deployment
    app.state.access_verifier = access_verifier
    app.state.websocket_counts = {"events": 0, "preview": 0}
    if deployment.effective_allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=list(deployment.effective_allowed_hosts),
        )
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    security = SecurityDependencies(
        lambda: store.load().security,
        deployment=deployment,
        access_verifier=access_verifier,
    )

    @app.middleware("http")
    async def production_headers(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-ID", "")
        if not request_id or len(request_id) > 128:
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        request_token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(request_token)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self' ws: wss:; frame-ancestors 'self'; base-uri 'self'"
        )
        if deployment.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(RevisionConflict)
    async def revision_conflict(_: Request, exc: RevisionConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Response:
        if deployment.auth_mode == "token":
            token = request.cookies.get(ADMIN_COOKIE)
            if token is None or not secrets.compare_digest(
                token, store.load().security.admin_token
            ):
                return RedirectResponse("/login", status_code=303)
        else:
            await security.require_admin(request)
        csrf = request.cookies.get(CSRF_COOKIE) or new_csrf_token()
        response = templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "csrf": csrf,
                "config": _safe_config(store.load()),
            },
        )
        if request.cookies.get(CSRF_COOKIE) is None:
            response.set_cookie(
                CSRF_COOKIE,
                csrf,
                httponly=False,
                secure=deployment.secure_cookies,
                samesite="strict",
            )
        return response

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        if deployment.auth_mode != "token":
            raise HTTPException(status_code=404, detail="local login is disabled")
        return templates.TemplateResponse(request, "login.html", {"error": False})

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request, token: str = Form()) -> Response:
        if deployment.auth_mode != "token":
            raise HTTPException(status_code=404, detail="local login is disabled")
        if not secrets.compare_digest(token, store.load().security.admin_token):
            return templates.TemplateResponse(
                request, "login.html", {"error": True}, status_code=401
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            ADMIN_COOKIE,
            token,
            httponly=True,
            secure=deployment.secure_cookies or request.url.scheme == "https",
            samesite="strict",
        )
        response.set_cookie(
            CSRF_COOKIE,
            new_csrf_token(),
            httponly=False,
            secure=deployment.secure_cookies or request.url.scheme == "https",
            samesite="strict",
        )
        return response

    @app.post("/logout")
    async def logout(_: None = Depends(security.require_admin_csrf)) -> Response:
        target = "/login"
        if deployment.auth_mode == "cloudflare_access":
            assert deployment.cloudflare_team_domain is not None
            target = (
                f"{deployment.cloudflare_team_domain.rstrip('/')}/cdn-cgi/access/logout"
            )
        response = RedirectResponse(target, status_code=303)
        response.delete_cookie(ADMIN_COOKIE)
        response.delete_cookie(CSRF_COOKIE)
        return response

    @app.get("/api/v1/health")
    async def health(_: None = Depends(security.require_read)) -> dict[str, Any]:
        snapshot = service.health.model_copy(deep=True)
        config = store.load()
        now = time.monotonic()
        frame_age = (
            None
            if service.metrics.last_frame_monotonic is None
            else now - service.metrics.last_frame_monotonic
        )
        snapshot.capture.details["frame_age_seconds"] = frame_age
        if frame_age is None or frame_age > config.source.stale_after_seconds:
            snapshot.capture.status = "down"
            snapshot.capture.message = "capture frame is stale"
            snapshot.status = "degraded"
        if config.regions:
            ocr_age = (
                None
                if service.metrics.last_ocr_monotonic is None
                else now - service.metrics.last_ocr_monotonic
            )
            snapshot.ocr.details["batch_age_seconds"] = ocr_age
            if ocr_age is None or ocr_age > config.source.stale_after_seconds:
                snapshot.ocr.status = "degraded"
                snapshot.ocr.message = "OCR result is stale"
                snapshot.status = "degraded"
        snapshot.outputs = {
            adapter_id: HealthComponent(
                status="degraded" if adapter.status.state == "degraded" else "ok",
                message=adapter.status.message,
                details=adapter.details(),
            )
            for adapter_id, adapter in output_manager.adapters.items()
        }
        if any(
            component.status == "degraded" for component in snapshot.outputs.values()
        ):
            snapshot.status = "degraded"
        return snapshot.model_dump(mode="json")

    @app.get("/livez")
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> Response:
        config = store.load()
        now = time.monotonic()
        frame_age = (
            None
            if service.metrics.last_frame_monotonic is None
            else now - service.metrics.last_frame_monotonic
        )
        capture_ready = (
            frame_age is not None and frame_age <= config.source.stale_after_seconds
        )
        ocr_age = (
            None
            if service.metrics.last_ocr_monotonic is None
            else now - service.metrics.last_ocr_monotonic
        )
        ocr_ready = not config.regions or (
            ocr_age is not None
            and ocr_age
            <= max(config.source.stale_after_seconds, 3.0 / config.ocr.target_hz)
        )
        ready = capture_ready and ocr_ready
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "not_ready",
                "capture": {"ready": capture_ready, "frame_age_seconds": frame_age},
                "ocr": {"ready": ocr_ready, "batch_age_seconds": ocr_age},
            },
        )

    @app.get("/api/v1/results")
    async def results(
        _: None = Depends(security.require_read),
    ) -> dict[str, Any] | None:
        return (
            service.latest_result.model_dump(mode="json")
            if service.latest_result
            else None
        )

    @app.get("/api/v1/regions/{region_id}/filter-preview")
    async def region_filter_preview(
        region_id: str, _: None = Depends(security.require_read)
    ) -> Response:
        preview = service.latest_region_previews.get(region_id)
        if preview is None:
            raise HTTPException(
                status_code=404, detail="filtered preview is not available"
            )
        return Response(
            content=preview,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/config")
    async def get_config(_: None = Depends(security.require_admin)) -> dict[str, Any]:
        return _safe_config(store.load())

    @app.put("/api/v1/config")
    async def put_config(
        payload: dict[str, Any], _: None = Depends(security.require_admin_csrf)
    ) -> dict[str, Any]:
        current = store.load()
        expected_revision = int(payload.get("revision", -1))
        merged = restore_redacted(dict(payload), current.model_dump(mode="python"))
        merged["security"] = current.security.model_dump(mode="python")
        try:
            candidate = ServiceConfig.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_context=False),
            ) from exc
        updated = store.replace(candidate, expected_revision)
        if start_runtime:
            await runtime.restart()
        await output_manager.stop()
        output_manager.configure(updated.outputs)
        await output_manager.start()
        return _safe_config(updated)

    @app.get("/api/v1/sources")
    async def sources(_: None = Depends(security.require_admin)) -> dict[str, Any]:
        discovered: list[dict[str, Any]] = []
        errors: list[str] = []
        for source_type, source in (
            ("decklink", DeckLinkCapture),
            ("opencv", OpenCVCapture),
            ("mock", MockCapture),
        ):
            try:
                devices = await asyncio.to_thread(source.discover)
                discovered.extend(
                    {"type": source_type, **asdict(device)} for device in devices
                )
            except DeckLinkUnavailable as exc:
                errors.append(str(exc))
            except Exception as exc:
                errors.append(f"{source_type}: {exc}")
        return {"devices": discovered, "errors": errors}

    @app.post("/api/v1/read-tokens")
    async def create_read_token(
        _: None = Depends(security.require_admin_csrf),
    ) -> dict[str, str]:
        current = store.load()
        token = secrets.token_urlsafe(32)
        updated_security = current.security.model_copy(
            update={"read_tokens": [*current.security.read_tokens, token]}
        )
        updated = current.model_copy(update={"security": updated_security})
        store.replace(updated, current.revision)
        return {"token": token}

    @app.get("/api/v1/profiles")
    async def list_profiles(_: None = Depends(security.require_admin)) -> list[str]:
        return profiles.list()

    @app.get("/api/v1/profiles/{name}")
    async def get_profile(
        name: str, _: None = Depends(security.require_admin)
    ) -> dict[str, Any]:
        try:
            return _safe_config(profiles.load(name))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc

    @app.put("/api/v1/profiles/{name}")
    async def save_profile(
        name: str, _: None = Depends(security.require_admin_csrf)
    ) -> dict[str, str]:
        try:
            profiles.save(name, store.load())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"name": name}

    @app.delete("/api/v1/profiles/{name}")
    async def delete_profile(
        name: str, _: None = Depends(security.require_admin_csrf)
    ) -> Response:
        try:
            profiles.delete(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        return Response(status_code=204)

    @app.post("/api/v1/profiles/{name}/activate")
    async def activate_profile(
        name: str, _: None = Depends(security.require_admin_csrf)
    ) -> dict[str, Any]:
        current = store.load()
        try:
            selected = profiles.load(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        selected = selected.model_copy(
            update={"revision": current.revision, "security": current.security}
        )
        updated = store.replace(selected, current.revision)
        if start_runtime:
            await runtime.restart()
        await output_manager.stop()
        output_manager.configure(updated.outputs)
        await output_manager.start()
        return _safe_config(updated)

    @app.post("/api/v1/runtime/restart")
    async def restart_runtime(
        _: None = Depends(security.require_admin_csrf),
    ) -> dict[str, str]:
        if start_runtime:
            await runtime.restart()
        return {"status": "restarted"}

    @app.get("/api/v1/outputs")
    async def outputs(_: None = Depends(security.require_admin)) -> dict[str, Any]:
        return {
            adapter_id: {
                "state": adapter.status.state,
                "message": adapter.status.message,
                "sent": adapter.status.sent,
                "failed": adapter.status.failed,
                "skipped": adapter.status.skipped,
                "consecutive_failures": adapter.status.consecutive_failures,
                "details": adapter.details(),
            }
            for adapter_id, adapter in output_manager.adapters.items()
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> Response:
        return Response(
            content=service.metrics.render(output_manager.adapters),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.websocket("/api/v1/events")
    async def event_socket(websocket: WebSocket) -> None:
        if not await security.websocket_allowed(websocket):
            await websocket.close(code=4401)
            return
        if app.state.websocket_counts["events"] >= deployment.websocket_limit:
            await websocket.close(code=4429)
            return
        app.state.websocket_counts["events"] += 1
        service.metrics.set_websocket("events", 1)
        await websocket.accept()
        try:
            if service.latest_result is not None:
                await websocket.send_json(service.latest_result.model_dump(mode="json"))
            async with service.results.subscribe() as queue:
                while True:
                    result = await queue.get()
                    await websocket.send_json(result.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            app.state.websocket_counts["events"] -= 1
            service.metrics.set_websocket("events", -1)

    @app.websocket("/api/v1/preview")
    async def preview_socket(websocket: WebSocket) -> None:
        if not await security.websocket_allowed(websocket):
            await websocket.close(code=4401)
            return
        if app.state.websocket_counts["preview"] >= deployment.websocket_limit:
            await websocket.close(code=4429)
            return
        app.state.websocket_counts["preview"] += 1
        service.metrics.set_websocket("preview", 1)
        await websocket.accept()
        try:
            if service.latest_preview is not None:
                preview = service.latest_preview
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "preview.meta",
                            "width": preview.width,
                            "height": preview.height,
                            "sequence": preview.sequence,
                        }
                    )
                )
                await websocket.send_bytes(preview.jpeg)
            async with service.preview_frames.subscribe() as queue:
                while True:
                    preview = await queue.get()
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "preview.meta",
                                "width": preview.width,
                                "height": preview.height,
                                "sequence": preview.sequence,
                            }
                        )
                    )
                    await websocket.send_bytes(preview.jpeg)
        except WebSocketDisconnect:
            pass
        finally:
            app.state.websocket_counts["preview"] -= 1
            service.metrics.set_websocket("preview", -1)

    @app.get("/preview/{layout}", response_class=HTMLResponse)
    async def scoreboard_preview(
        request: Request, layout: str, _: None = Depends(security.require_read)
    ) -> Response:
        return templates.TemplateResponse(
            request, "scoreboard.html", {"layout": layout}
        )

    return app
