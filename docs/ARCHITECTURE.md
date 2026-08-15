# Service architecture

```text
Capture adapter ──latest frame──> frame transform ──> OCR regions
      │                                   │               │
      │                                   └──5 FPS JPEG──> preview WebSocket
      │                                                   │
      └── health                              normalized result batch
                                                               │
                                         latest-value event bus
                                      ┌────────┼───────────┬────────┐
                                      │        │           │        │
                                  TV WS     vMix/UNO    webhook   files
```

The capture/OCR path never waits for an output. Every subscriber receives a queue of
size one, so a slow consumer loses superseded intermediate batches instead of adding
latency. Output adapters add bounded exponential backoff after failures.

Domain models, geometry, configuration, capture contracts, OCR contracts, and output
contracts have no Qt dependency. The original PySide application can therefore remain
available during migration without constraining the service.

Profiles and the active configuration are schema-versioned JSON validated by Pydantic.
Writes use a temporary file, `fsync`, and atomic replacement. Region and transform
coordinates are normalized so profiles do not depend on preview resolution.

The default API boundary is FastAPI/Jinja with a small browser canvas module. Browser
sessions use an HttpOnly administrator cookie plus double-submit CSRF token; machine
consumers use scoped read tokens. There is no network shutdown endpoint.

