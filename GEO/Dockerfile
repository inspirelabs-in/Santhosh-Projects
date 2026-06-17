FROM python:3.12-slim

WORKDIR /app

# System deps for Camoufox (headless Firefox-based browser)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 libdbus-glib-1-2 libxt6 libx11-xcb1 \
    libasound2 libpci3 libxcomposite1 libxdamage1 \
    libxrandr2 libxfixes3 libxcursor1 libxi6 \
    libxtst6 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libgbm1 libpango-1.0-0 \
    libcairo2 libnspr4 libnss3 libxshmfence1 \
    fonts-liberation xvfb && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Camoufox browser binary + geoip data (as root so it can write to site-packages)
RUN python -m camoufox fetch

# Create app user and copy Camoufox cache from root to geouser
RUN groupadd --gid 2001 geo && \
    useradd --uid 2001 --gid geo --shell /bin/bash --create-home geouser && \
    mkdir -p /home/geouser/.cache && \
    cp -r /root/.cache/camoufox /home/geouser/.cache/camoufox && \
    chown -R geouser:geo /home/geouser/.cache && \
    chown -R geouser:geo /app && \
    chmod -R a+r /usr/local/lib/python3.12/site-packages/camoufox/

USER geouser

COPY --chown=geouser:geo app/ app/
COPY --chown=geouser:geo scripts/ scripts/
COPY --chown=geouser:geo Public/ Public/
COPY --chown=geouser:geo run.py .
COPY --chown=geouser:geo schema.sql .
COPY --chown=geouser:geo system_docs.md .

EXPOSE 8000

CMD ["python", "run.py"]
