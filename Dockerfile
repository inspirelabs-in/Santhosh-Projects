# -- Python backend --
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir . && \
    find /usr/local/lib -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true

EXPOSE 8000

CMD ["uvicorn", "grabon_intel.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
