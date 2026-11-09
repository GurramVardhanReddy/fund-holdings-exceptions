FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fundx/ fundx/
COPY scripts/ scripts/
COPY config/ config/

# Run as a non-root user; data/ and reports/ are meant to be mounted volumes.
RUN useradd --create-home app && mkdir -p data reports logs && chown -R app /app
USER app

ENTRYPOINT ["bash", "scripts/run_daily.sh"]
