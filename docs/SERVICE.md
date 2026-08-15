# ScoreSight service

## Local development

Use an isolated Python 3.11 or 3.12 environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src/scoresight tests
python -m mypy -p scoresight
```

Start the service with an explicit configuration file:

```powershell
scoresight-service --config .\local-config.json
```

The first start creates a validated configuration and a random administrator token.
Open `http://127.0.0.1:18099`, then sign in with `security.admin_token` from the file.
The token is deliberately omitted from API configuration responses.

Binding to `0.0.0.0` is an explicit deployment decision. Create a read-only token with
`POST /api/v1/read-tokens` and use it for the Lightning TV consumer or HTML preview.
Do not expose the service directly to the public internet.

## Runtime dependencies

The web/configuration service installs from the base project dependencies. Actual OCR
also needs the `ocr` extra and native Tesseract libraries:

```powershell
python -m pip install -e ".[ocr]"
$env:SCORESIGHT_TESSDATA = "C:\path\to\tessdata"
```

Windows DeckLink capture additionally requires Blackmagic Desktop Video and the
`scoresight_decklink` native extension described in `DECKLINK.md`. Without those
components the service stays available and reports capture health as degraded.

## API

- `GET /api/v1/health` and `GET /api/v1/results` provide snapshots.
- `WS /api/v1/events` publishes an initial snapshot followed by latest-only batches.
- `WS /api/v1/preview` carries metadata and JPEG preview frames.
- `/api/v1/config`, `/sources`, `/profiles`, and `/outputs` support the operator UI.
- `/preview/default?token=...` is the transparent HTML scoreboard view.
- `/metrics` exposes Prometheus text metrics.

Configuration writes use revisions. A stale write returns HTTP 409 rather than
silently overwriting a newer operator change.

## Packaging

Build a Windows service bundle with:

```powershell
uv sync --extra ocr
uv pip install pyinstaller
uv run pyinstaller --clean --noconfirm scoresight-service.spec
```

The DeckLink extension and Blackmagic Desktop Video runtime remain external native
prerequisites. For Linux, install the wheel into `/opt/scoresight/.venv`, copy the
example `packaging/systemd/scoresight.service`, and keep configuration/tessdata under
`/var/lib/scoresight`.
