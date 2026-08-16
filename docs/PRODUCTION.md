# Production deployment

This deployment keeps the proprietary DeckLink driver and capture bridge in the Ubuntu VM.
ScoreSight receives a private RTSP stream and remains a portable, single-process container.

## 1. Host and DeckLink preflight

Allocate 6 vCPU and 10 GB RAM to the Ubuntu VM and use the Proxmox `host` CPU type. Enable
IOMMU on the Proxmox host, confirm that the Mini Recorder HD has a clean IOMMU group, and pass
the complete PCI function to the VM. Do not enable an ACS override to force isolation. If the
card cannot be isolated, use an external SDI encoder instead.

Install Blackmagic Desktop Video 16.0.1 in the VM, reboot, and verify the driver and firmware:

```bash
sudo BlackmagicFirmwareUpdater status
ls -l /dev/blackmagic
```

Download the Desktop Video SDK 16.0 from Blackmagic. The SDK license requires this manual
download; it must not be committed to the repository. Install build prerequisites and build the
pinned FFmpeg release:

```bash
sudo apt-get update
sudo apt-get install -y build-essential curl gpg libx264-dev pkg-config xz-utils
./packaging/decklink/build-ffmpeg.sh /srv/vendor/Blackmagic_DeckLink_SDK_16.0
sudo install -m 0755 packaging/decklink/decklink-bridge.sh \
  /opt/scoresight-decklink/bin/decklink-bridge.sh
FFMPEG_BIN=/opt/scoresight-decklink/bin/ffmpeg \
  ./packaging/decklink/probe-decklink.sh
```

Copy the exact format code reported for the camera into `/etc/scoresight/decklink-bridge.env`.
Start at 30 output frames per second. Retest at 60 only if an LED refresh/shutter recording shows
missing or partially illuminated segments. Set `DECKLINK_INTERLACED=true` only for an interlaced
input.

Install and start the bridge after the Docker stack is running:

```bash
sudo useradd --system --no-create-home --groups video scoresight-capture
sudo install -d -m 0750 /etc/scoresight
sudo install -m 0640 packaging/decklink/decklink-bridge.env.example \
  /etc/scoresight/decklink-bridge.env
sudo install -m 0644 packaging/decklink/scoresight-decklink-bridge.service \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scoresight-decklink-bridge.service
```

## 2. Docker deployment

Create the external Nginx Proxy Manager network if it does not already exist, then copy the
environment template:

```bash
docker network inspect proxy >/dev/null 2>&1 || docker network create proxy
cp .env.production.example .env.production
```

In Portainer, set `SCORESIGHT_FAN_SITE_TOKEN` to the fan-site ingest token. Environment variables
are visible to Portainer administrators and through `docker inspect`, so restrict Docker and
Portainer access and never commit the populated `.env.production` file.

Set `SCORESIGHT_IMAGE` to the immutable digest emitted by the successful tagged release workflow.
Also set the Cloudflare team domain, Access application audience, public URL, hostname, origin and
NPM network in `.env.production`. Validate and launch the stack:

```bash
docker compose --env-file .env.production -f compose.production.yml config --quiet
docker compose --env-file .env.production -f compose.production.yml pull
docker compose --env-file .env.production -f compose.production.yml up -d
curl --fail http://127.0.0.1:8554/ >/dev/null || true
docker compose --env-file .env.production -f compose.production.yml ps
```

The ScoreSight port is exposed only to Docker networks; do not publish port 18099 on the VM.
Configure NPM to proxy the dedicated hostname to `scoresight:18099`, enable WebSockets, and add
the directives in `packaging/nginx/scoresight-advanced.conf`.

On first start the container seeds `/var/lib/scoresight/config-v1.json` with the private RTSP
source. Configure OCR regions in the operator UI. Configure the fan-site output to read the token
from the environment without copying its value into ScoreSight configuration:

```json
{
  "kind": "fan_site",
  "enabled": true,
  "settings": {
    "endpoint": "wss://fan.example/ws/ocr",
    "stream_id": "stream1",
    "token_env": "SCORESIGHT_FAN_SITE_TOKEN"
  },
  "field_mapping": {
    "Clock": "Clock.Text",
    "Home Score": "score_a",
    "Away Score": "score_b",
    "Period": "period"
  }
}
```

## 3. Cloudflare and Nginx

Create a Cloudflare Access self-hosted application covering the entire ScoreSight hostname. Use an
allow policy for the authorised operators and require the organisation's normal MFA policy. Use
Full (Strict) TLS between Cloudflare and NPM.

The application validates `Cf-Access-Jwt-Assertion` itself. NPM must preserve this header and must
not synthesize it. Set the VM firewall to accept public HTTP/HTTPS only from the published
Cloudflare IPv4 and IPv6 ranges. Refresh those rules as part of firewall maintenance. Direct
origin access with a forged Host header must fail.

Create an NPM custom location for `/metrics` that returns 404. `/livez` and `/readyz` contain no
secrets but should remain behind Access at the public edge. Prometheus should scrape `/metrics`
over the private Docker/VM network.

## 4. Health, monitoring and recovery

- `/livez` confirms that the web process is responsive. Docker uses it so loss of an SDI signal
  does not cause a restart loop.
- `/readyz` requires a frame newer than `source.stale_after_seconds` and, when regions exist, a
  recent OCR batch.
- `/api/v1/health` includes capture, OCR and output adapter state for an authenticated operator.
- `/metrics` provides Prometheus process, capture, OCR, WebSocket and output metrics.

Alert when readiness is false for 60 seconds, frame age exceeds 10 seconds, the fan-site output is
degraded or its acknowledgement age exceeds 60 seconds, or the container restarts. Output failure
must not restart a healthy capture pipeline.

Install the backup unit and timer after adjusting the volume path if the Compose project name was
changed:

```bash
sudo install -m 0755 packaging/operations/backup-scoresight.sh /opt/scoresight/packaging/operations/
sudo install -m 0644 packaging/operations/scoresight-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scoresight-backup.timer
```

Before an upgrade, take a backup and record the running image digest. Deploy only an image digest
that passed CI. Roll back by restoring the previous digest; restore configuration only if its
format changed.

## 5. Acceptance gate

Before live use:

1. Record representative footage and retain at least 200 labelled frames covering every digit,
   clock punctuation, perspective and lighting condition.
2. Confirm at least 99% precision for accepted values and no more than one false accepted update
   per 30-minute recording.
3. Confirm p95 capture-to-result latency below 750 ms.
4. Disconnect and reconnect SDI, restart MediaMTX, restart the bridge, interrupt fan-site egress,
   and confirm automatic recovery within 30 seconds after the dependency returns.
5. Run a 24-hour soak. VM CPU must remain below 80%, memory must remain bounded, and neither the
   capture bridge nor ScoreSight may require a manual restart.

The physical passthrough, real camera format, OCR corpus and 24-hour soak cannot be validated in
repository CI; record their results as release evidence for the deployed host.
