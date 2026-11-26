FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY data /app/data
COPY validation /app/validation
COPY docs/dashboard.html /app/docs/dashboard.html

# D-01: LCIS_SECRET is NOT baked into the image. The process refuses to start in production without a
# random secret of at least 32 characters supplied at runtime (LCIS_ENV=production).
# D-20: the server runs as an unprivileged user and exposes a health check.
ENV LCIS_PRODUCT_ROOT=/app \
    LCIS_DB_PATH=/var/lib/lcis/lcis.db \
    LCIS_ENV=production
RUN useradd --system --uid 10001 --create-home lcis \
    && mkdir -p /var/lib/lcis \
    && chown -R lcis:lcis /var/lib/lcis /app
VOLUME /var/lib/lcis
EXPOSE 8000
USER lcis
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
