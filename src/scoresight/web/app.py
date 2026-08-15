from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from scoresight.capture.decklink import DeckLinkCapture, DeckLinkUnavailable
from scoresight.capture.mock import MockCapture
from scoresight.capture.opencv import OpenCVCapture
from scoresight.core.config import ConfigStore, RevisionConflict
from scoresight.core.models import ServiceConfig
from scoresight.core.profiles import ProfileStore
from scoresight.core.runtime import RuntimeController
from scoresight.core.service import ScoreSightService
from scoresight.outputs.manager import OutputManager
from scoresight.web.security import (
    ADMIN_COOKIE,
    CSRF_COOKIE,
    SecurityDependencies,
    new_csrf_token,
)

PACKAGE_DIR = Path(__file__).resolve().parent


def _safe_config(config: ServiceConfig) -> dict[str, Any]:
    payload = config.model_dump(mode="json")
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
) -> FastAPI:
    store = ConfigStore(config_path)
    profiles = ProfileStore(profile_path)
    service = ScoreSightService(store)
    output_manager = OutputManager(service.results)
    runtime = RuntimeController(service, store)

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

    app = FastAPI(title="ScoreSight", version="0.2.0", lifespan=lifespan)
    app.state.service = service
    app.state.config_store = store
    app.state.profile_store = profiles
    app.state.output_manager = output_manager
    app.state.runtime = runtime
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    security = SecurityDependencies(lambda: store.load().security)

    @app.exception_handler(RevisionConflict)
    async def revision_conflict(_: Request, exc: RevisionConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Response:
        token = request.cookies.get(ADMIN_COOKIE)
        if token is None or not secrets.compare_digest(
            token, store.load().security.admin_token
        ):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "csrf": request.cookies.get(CSRF_COOKIE, ""),
                "config": _safe_config(store.load()),
            },
        )

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        return templates.TemplateResponse(request, "login.html", {"error": False})

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request, token: str = Form()) -> Response:
        if not secrets.compare_digest(token, store.load().security.admin_token):
            return templates.TemplateResponse(
                request, "login.html", {"error": True}, status_code=401
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            ADMIN_COOKIE,
            token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        response.set_cookie(
            CSRF_COOKIE,
            new_csrf_token(),
            httponly=False,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return response

    @app.post("/logout")
    async def logout(_: None = Depends(security.require_admin_csrf)) -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(ADMIN_COOKIE)
        response.delete_cookie(CSRF_COOKIE)
        return response

    @app.get("/api/v1/health")
    async def health(_: None = Depends(security.require_read)) -> dict[str, Any]:
        return service.health.model_dump(mode="json")

    @app.get("/api/v1/results")
    async def results(_: None = Depends(security.require_read)) -> dict[str, Any] | None:
        return service.latest_result.model_dump(mode="json") if service.latest_result else None

    @app.get("/api/v1/config")
    async def get_config(_: None = Depends(security.require_admin)) -> dict[str, Any]:
        return _safe_config(store.load())

    @app.put("/api/v1/config")
    async def put_config(
        payload: dict[str, Any], _: None = Depends(security.require_admin_csrf)
    ) -> dict[str, Any]:
        current = store.load()
        expected_revision = int(payload.get("revision", -1))
        merged = dict(payload)
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
                discovered.extend({"type": source_type, **asdict(device)} for device in devices)
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
    async def get_profile(name: str, _: None = Depends(security.require_admin)) -> dict[str, Any]:
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
    async def delete_profile(name: str, _: None = Depends(security.require_admin_csrf)) -> Response:
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
            }
            for adapter_id, adapter in output_manager.adapters.items()
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics(_: None = Depends(security.require_read)) -> str:
        latest_sequence = service.latest_result.sequence if service.latest_result else 0
        runtime_metrics = service.metrics
        return "\n".join(
            [
                "# TYPE scoresight_result_sequence gauge",
                f"scoresight_result_sequence {latest_sequence}",
                "# TYPE scoresight_event_subscribers gauge",
                f"scoresight_event_subscribers {service.results.subscriber_count}",
                "# TYPE scoresight_capture_frames_total counter",
                f"scoresight_capture_frames_total {runtime_metrics.capture_frames}",
                "# TYPE scoresight_preview_frames_total counter",
                f"scoresight_preview_frames_total {runtime_metrics.preview_frames}",
                "# TYPE scoresight_ocr_batches_total counter",
                f"scoresight_ocr_batches_total {runtime_metrics.ocr_batches}",
                "# TYPE scoresight_ocr_latency_ms gauge",
                f"scoresight_ocr_latency_ms {runtime_metrics.last_ocr_latency_ms}",
                "",
            ]
        )

    @app.websocket("/api/v1/events")
    async def event_socket(websocket: WebSocket) -> None:
        if not security.websocket_allowed(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        if service.latest_result is not None:
            await websocket.send_json(service.latest_result.model_dump(mode="json"))
        async with service.results.subscribe() as queue:
            while True:
                result = await queue.get()
                await websocket.send_json(result.model_dump(mode="json"))

    @app.websocket("/api/v1/preview")
    async def preview_socket(websocket: WebSocket) -> None:
        if not security.websocket_allowed(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
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

    @app.get("/preview/{layout}", response_class=HTMLResponse)
    async def scoreboard_preview(
        request: Request, layout: str, _: None = Depends(security.require_read)
    ) -> Response:
        return templates.TemplateResponse(request, "scoreboard.html", {"layout": layout})

    return app
