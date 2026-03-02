"""GCS helpers — used for MLflow SQLite sync and model artifacts."""

from pathlib import Path
from google.cloud import storage
from . import config


def get_client() -> storage.Client:
    return storage.Client(project=config.GCP_PROJECT_ID)


def upload(local_path: str | Path, blob_name: str) -> None:
    client = get_client()
    bucket = client.bucket(config.GCS_BUCKET)
    bucket.blob(blob_name).upload_from_filename(str(local_path))


def download(blob_name: str, local_path: str | Path) -> None:
    client = get_client()
    bucket = client.bucket(config.GCS_BUCKET)
    bucket.blob(blob_name).download_to_filename(str(local_path))
