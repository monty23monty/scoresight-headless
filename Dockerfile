# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:a116514e19457bcb7af7efe9c3dd0b9b71e85b317694e7882a1c52aa15a78134

FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION=0.10.9
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential libtesseract-dev libleptonica-dev pkg-config \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/scoresight
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra ocr --no-editable

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=0.2.0.dev0
ARG REVISION=unknown
LABEL org.opencontainers.image.title="ScoreSight" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/monty23monty/scoresight-headless"
ENV PATH=/opt/scoresight/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SCORESIGHT_DATA_DIR=/var/lib/scoresight \
    SCORESIGHT_TESSDATA=/opt/scoresight/tesseract/tessdata
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libglib2.0-0 libgomp1 liblept5 libtesseract5 tini \
    && python -m pip uninstall --yes setuptools wheel pip \
    && groupadd --gid 10001 scoresight \
    && useradd --uid 10001 --gid scoresight --home-dir /nonexistent --shell /usr/sbin/nologin scoresight \
    && install -d -o scoresight -g scoresight /var/lib/scoresight \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/scoresight
COPY --from=builder /opt/scoresight/.venv ./.venv
COPY tesseract ./tesseract
COPY packaging/docker/config-v1.production.json ./config-v1.production.json
COPY packaging/docker/docker-entrypoint.sh /usr/local/bin/scoresight-entrypoint
RUN chmod 0755 /usr/local/bin/scoresight-entrypoint
USER 10001:10001
EXPOSE 18099
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18099/livez', timeout=2).read()"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/scoresight-entrypoint"]
CMD ["scoresight-service", "--host", "0.0.0.0", "--port", "18099", "--data-dir", "/var/lib/scoresight"]
