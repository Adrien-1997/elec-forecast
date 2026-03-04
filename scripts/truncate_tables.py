"""Truncate all pipeline BQ tables. Run before a full backfill reset."""
from google.cloud import bigquery
from elec_jobs.shared import config

client = bigquery.Client(project=config.GCP_PROJECT_ID)

tables = [
    "elec_raw.eco2mix",
    "elec_raw.weather",
    "elec_features.features",
    "elec_ml.predictions",
    "elec_ml.metrics",
]

for t in tables:
    fqn = f"`{config.GCP_PROJECT_ID}.{t}`"
    client.query(f"DELETE FROM {fqn} WHERE TRUE").result()
    print(f"  cleared {t}")

print("done")
