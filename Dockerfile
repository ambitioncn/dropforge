FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 65532 dropforge \
    && useradd --uid 65532 --gid dropforge --home-dir /nonexistent --no-create-home dropforge \
    && install -d -o dropforge -g dropforge -m 0700 /data \
    && install -d -o root -g dropforge -m 0750 /config
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER 65532:65532
WORKDIR /data
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/api/status', timeout=2).read(1)"]
ENTRYPOINT ["dropforge"]
CMD ["--config", "/config/drops.toml", "dashboard", "--host", "127.0.0.1"]
