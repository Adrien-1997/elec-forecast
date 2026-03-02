"""BigQuery helpers."""

from google.cloud import bigquery

_client: bigquery.Client | None = None


def get_client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client()
    return _client


def load_dataframe(df, table_ref: str, write_disposition: str = "WRITE_APPEND") -> None:
    """Write a pandas DataFrame to a BQ table."""
    client = get_client()
    job_config = bigquery.LoadJobConfig(
        write_disposition=write_disposition,
        autodetect=False,
    )
    job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
    job.result()
