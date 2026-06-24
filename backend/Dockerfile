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

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]

FROM python:3.11-slim AS builder

WORKDIR /app

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
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app
COPY entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
