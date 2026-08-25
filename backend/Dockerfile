FROM python:3.11-slim AS development

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    redis-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements/dev.txt

COPY pyproject.toml ./
COPY entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh
COPY app/ app/

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
# Pin the outbound TLS trust store: Python defaults to certifi (Mozilla-based), which has
# removed legacy roots such as Baltimore/AAA/old GlobalSign/old DigiCert, while several
# real-world sites still chain to them. Use the Debian system CA bundle (more complete).
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]

# Hybrid cross-build, part 1 (see docs/design/cd-workflow-optimization.md §5.3):
# pre-download the arm/v7 wheels for every package that has one
# (requirements/prod-binary.txt) on the NATIVE build platform — this runs on
# amd64 without QEMU, so it is fast. Only when targeting linux/arm/v7; for any
# other target platform /wheels is left empty and the builder behaves exactly
# as before this stage existed.
FROM --platform=$BUILDPLATFORM python:3.11-slim AS wheel-fetcher

ARG TARGETPLATFORM

WORKDIR /app

COPY requirements/prod-binary.txt requirements/prod-binary.txt

RUN mkdir -p /wheels && \
    if [ "$TARGETPLATFORM" = "linux/arm/v7" ]; then \
      pip install --no-cache-dir --upgrade pip && \
      pip download --no-deps --dest /wheels \
        --platform manylinux2014_armv7l \
        --platform manylinux_2_17_armv7l \
        --platform manylinux_2_28_armv7l \
        --platform manylinux_2_31_armv7l \
        --python-version 311 --implementation cp --abi cp311 \
        --only-binary=:all: -r requirements/prod-binary.txt; \
    fi

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Hybrid cross-build, part 2: /wheels is pre-populated for arm/v7 (empty on all
# other targets). --find-links prefers the local wheels, sparing the QEMU
# emulation from per-package network downloads; packages without wheels
# (uvloop/asyncpg/cffi/greenlet/httptools/psutil/markupsafe/pyyaml, see
# requirements/prod-binary.txt header) still compile from PyPI sdists here.
# With an empty directory this is equivalent to the previous plain install.
COPY --from=wheel-fetcher /wheels /wheels
COPY requirements/ requirements/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --find-links /wheels -r requirements/prod.txt

COPY pyproject.toml ./
COPY app/ app/

FROM python:3.11-slim AS production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    redis-tools \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app
COPY entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
# Pin the outbound TLS trust store: Python defaults to certifi (Mozilla-based), which has
# removed legacy roots such as Baltimore/AAA/old GlobalSign/old DigiCert, while several
# real-world sites still chain to them. Use the Debian system CA bundle (more complete).
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
