FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Python dependencies are pinned in requirements.txt; Debian package versions
# stay on the base image security stream to avoid brittle distro-specific pins.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping libcap2-bin \
    && setcap cap_net_raw+ep /usr/bin/ping \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN addgroup --system app && adduser --system --ingroup app app

COPY . .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
