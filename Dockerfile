FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY data /app/data
COPY validation /app/validation

# PRODUCT_ROOT points the API at the shipped EIA/NIST data and the validation results; the SQLite
# database is written to /var/lib/lcis (mount a volume there in production).
ENV LCIS_PRODUCT_ROOT=/app \
    LCIS_DB_PATH=/var/lib/lcis/lcis.db \
    LCIS_SECRET=change-me-in-production
RUN mkdir -p /var/lib/lcis
VOLUME /var/lib/lcis
EXPOSE 8000

# NFR-03: TLS is terminated by the reverse proxy in front of this container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
