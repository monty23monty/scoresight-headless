# Blackmagic DeckLink integration

ScoreSight loads DeckLink support from an optional `scoresight_decklink` Python
extension. The proprietary Blackmagic SDK and Desktop Video runtime are intentionally
not vendored in this repository.

The extension contract is:

```python
scoresight_decklink.discover() -> list[dict]
capture = scoresight_decklink.Capture(device_id, mode_id, ring_size=2)
capture.start()
frame = capture.read_latest(timeout_seconds)
capture.stop()
```

Each discovered device contains `id`, `name`, and `modes`; each mode contains `id`,
`width`, `height`, `frames_per_second`, and `pixel_format`. Frames expose `sequence`,
`image` as an owned NumPy-compatible buffer, `width`, `height`, `captured_at`,
`monotonic_ns`, and `pixel_format`.

The native implementation must use an `IDeckLinkInputCallback`, copy or retain frames
into a two-slot owned ring, and wake `read_latest` without calling Python on the SDK
callback thread. When both slots are occupied, overwrite the older slot. Initially gate
1080p25 and 1080p30 8-bit YUV modes on Windows.

Hardware acceptance requires a Windows runner with a DeckLink card and Desktop Video:

```powershell
$env:SCORESIGHT_DECKLINK_HARDWARE = "1"
python -m pytest -m decklink_hardware
```

The hardware-independent suite injects a fake module and verifies the complete Python
binding contract. Building and validating the actual extension is blocked until the
Blackmagic SDK/runtime and target card are available on the build host.

