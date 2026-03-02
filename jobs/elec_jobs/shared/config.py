"""Shared configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

GCP_PROJECT_ID = os.environ["GCP_PROJECT_ID"]
GCP_REGION = os.getenv("GCP_REGION", "europe-west9")
GCS_BUCKET = os.environ["GCS_BUCKET"]

BQ_DATASET_RAW = os.getenv("BQ_DATASET_RAW", "elec_raw")
BQ_DATASET_FEATURES = os.getenv("BQ_DATASET_FEATURES", "elec_features")
BQ_DATASET_ML = os.getenv("BQ_DATASET_ML", "elec_ml")

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT_NAME", "elec-forecast")

ODRE_BASE_URL = "https://odre.opendatasoft.com/api/explore/v2.1"
ODRE_REALTIME_DATASET = "eco2mix-regional-tr"
ODRE_HISTORICAL_DATASET = "eco2mix-regional-cons-def"

# code_insee_region → (libelle_region, latitude, longitude)
# 12 metropolitan French regions as they appear in the ODRÉ API.
# Centroids are approximate geographic centres used for weather queries.
REGION_CENTROIDS: dict[str, tuple[str, float, float]] = {
    "11": ("Île-de-France",              48.8566,  2.3522),
    "24": ("Centre-Val de Loire",         47.7516,  1.6751),
    "27": ("Bourgogne-Franche-Comté",    47.2805,  4.9994),
    "28": ("Normandie",                   49.1829,  0.3707),
    "32": ("Hauts-de-France",             50.4902,  2.7857),
    "44": ("Grand Est",                   48.6994,  6.1867),
    "52": ("Pays de la Loire",            47.7624, -0.3296),
    "53": ("Bretagne",                    48.2020, -2.9326),
    "75": ("Nouvelle-Aquitaine",          44.8378,  0.5792),
    "76": ("Occitanie",                   43.8485,  3.2503),
    "84": ("Auvergne-Rhône-Alpes",       45.7597,  4.8422),
    "93": ("Provence-Alpes-Côte d'Azur", 43.9352,  6.0679),
}
