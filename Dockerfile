FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Keep the runtime small; NET_RAW is granted only to the separate worker.
# hadolint ignore=DL3018
RUN apk upgrade --no-cache && apk add --no-cache iputils-ping

COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: --upgrade pip==26.2.2 \
    && pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && pip uninstall -y pip

RUN addgroup -S app && adduser -S -G app app

# Runtime allowlist: tests, development tools, secrets and deployment files stay outside.
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini LICENSE ./

ARG VERSION=local
ARG REVISION=unknown
ARG SOURCE=https://github.com/lasnet/IPTable
LABEL org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.source=$SOURCE \
      org.opencontainers.image.licenses=MIT

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
