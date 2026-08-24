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

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements/prod.txt

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
