# ScoreSight service

For the hardened Docker, Cloudflare Access, Nginx Proxy Manager and DeckLink bridge deployment,
see [Production deployment](PRODUCTION.md).

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
also needs the `ocr` extra:

```powershell
uv sync --extra ocr
$env:SCORESIGHT_TESSDATA = "C:\path\to\tessdata"
```

On 64-bit Windows with Python 3.11 through 3.14, the locked dependency uses a
hash-pinned, self-contained `tesserocr` wheel, so a separate system Tesseract install
is not required. Stop a running `scoresight-service` before syncing because Windows
locks its generated executable. Linux and macOS use the normal PyPI package and need
the platform Tesseract development libraries described by `tesserocr`.

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

## OCR setup workflow

Apply four-corner perspective correction before drawing OCR regions. The rectified
preview keeps the selected quadrilateral's natural aspect ratio, and the browser
canvas follows that ratio so overlay coordinates match the image sent to OCR. A
crop or perspective change clears existing regions after confirmation because it
creates a new coordinate space; save the transform and then redraw the regions.

For each region, choose `Clock / time`, `Number / score`, or `Free text`. Clock and
number fields apply a Tesseract character whitelist and built-in validation in
addition to the optional regular expression. `Confirm frames` controls how many
consecutive matching candidates are required before a new value becomes accepted.
The selected-region panel shows the exact filtered OCR input, the current candidate,
and the stable last accepted value.

## Live fan-site WebSocket output

Configure ScoreSight as the OCR client for the fan site's `/ws/ocr/<stream_id>/`
endpoint with an output entry like this:

```json
{
  "kind": "fan_site",
  "enabled": true,
  "settings": {
    "endpoint": "wss://fan.example/ws/ocr",
    "stream_id": "stream1",
    "token": "OCR_INGEST_TOKEN"
  },
  "field_mapping": {
    "Clock": "Clock.Text",
    "Home Score": "score_a",
    "Away Score": "score_b",
    "Period": "period"
  },
  "send_unchanged": false
}
```

The adapter authenticates with `Authorization: Bearer`, sends the optional OCR
registration frame, and then sends accepted values as
`{"type":"ocr","values":{...}}`. It keeps the connection open, checks every
registration and update acknowledgement, reconnects with exponential backoff, and
resends the full stable state after reconnecting. `GET /api/v1/outputs` exposes the
latest saved, ignored, conflict, mode, and game information returned by the fan site.

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
