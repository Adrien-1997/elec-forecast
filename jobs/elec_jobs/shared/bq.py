"""BigQuery helpers."""

import logging

from google.cloud import bigquery

LOG = logging.getLogger(__name__)

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


def merge_to_bq(
    client: bigquery.Client,
    df,
    project_id: str,
    table: str,
    key_cols: tuple = ("date_heure", "region"),
) -> None:
    """Upsert a DataFrame into a BQ table via MERGE on key_cols.

    Loads df into a temp table (WRITE_TRUNCATE), then merges into the target
    so that re-fetched or re-computed rows update existing slots rather than
    creating duplicates.
    """
    fq_target = f"`{project_id}.{table}`"
    fq_tmp    = f"`{project_id}.{table}_tmp`"

    client.load_table_from_dataframe(
        df,
        f"{project_id}.{table}_tmp",
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    ).result()

    non_key = [c for c in df.columns if c not in key_cols]
    on_clause     = " AND ".join(f"T.{c} = S.{c}" for c in key_cols)
    update_clause = ", ".join(f"T.{c} = S.{c}" for c in non_key)
    insert_cols   = ", ".join(df.columns)
    insert_vals   = ", ".join(f"S.{c}" for c in df.columns)

    sql = f"""
    MERGE {fq_target} T
    USING {fq_tmp} S
    ON {on_clause}
    WHEN MATCHED THEN UPDATE SET {update_clause}
    WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
    """
    client.query(sql).result()
    LOG.debug("merge_to_bq: %s", table)
