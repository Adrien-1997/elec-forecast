FROM python:3.11-slim

RUN pip install --no-cache-dir mlflow[extras]==2.14.3 google-cloud-storage

# SQLite DB lives on GCS; entrypoint script syncs it down before starting the server.
COPY apps/mlflow/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
