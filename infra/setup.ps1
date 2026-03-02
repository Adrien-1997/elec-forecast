# setup.ps1 -- one-time GCP project bootstrap for elec-forecast
# Prerequisites:
#   gcloud auth login
#   gcloud auth application-default login
#   gcloud config set project elec-forecast
#
# Run from repo root: .\infra\setup.ps1

param(
    [string]$ProjectId      = "elec-forecast",
    [string]$Region         = "europe-west9",
    [string]$ServiceAccount = "elec-forecast-sa"
)

$SaEmail = "$ServiceAccount@$ProjectId.iam.gserviceaccount.com"

# ---- 0. Resolve project number (needed for bucket name) ----
Write-Host "`n==> Resolving project number..."
$ProjectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"
$Bucket = "elec-forecast-$ProjectNumber"
Write-Host "    Project number : $ProjectNumber"
Write-Host "    Bucket name    : $Bucket"

# ---- 1. Enable APIs ----
Write-Host "`n==> Enabling APIs..."
gcloud services enable `
    bigquery.googleapis.com `
    storage.googleapis.com `
    run.googleapis.com `
    cloudbuild.googleapis.com `
    cloudscheduler.googleapis.com `
    artifactregistry.googleapis.com `
    secretmanager.googleapis.com `
    --project $ProjectId

# ---- 2. Service account ----
Write-Host "`n==> Creating service account: $SaEmail"
gcloud iam service-accounts describe $SaEmail --project $ProjectId 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    gcloud iam service-accounts create $ServiceAccount `
        --display-name "elec-forecast jobs SA" `
        --project $ProjectId
} else {
    Write-Host "    Already exists -- skipping creation."
}

Write-Host "==> Granting IAM roles..."
$Roles = @(
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/storage.objectAdmin",
    "roles/run.invoker",
    "roles/secretmanager.secretAccessor"
)
foreach ($Role in $Roles) {
    gcloud projects add-iam-policy-binding $ProjectId `
        --member "serviceAccount:$SaEmail" `
        --role $Role `
        --condition None | Out-Null
    Write-Host "    Granted: $Role"
}

# ---- 3. GCS bucket ----
Write-Host "`n==> Creating GCS bucket: gs://$Bucket"
gcloud storage buckets describe "gs://$Bucket" --project $ProjectId 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    gcloud storage buckets create "gs://$Bucket" --project $ProjectId --location $Region
} else {
    Write-Host "    Already exists -- skipping creation."
}

# ---- 4. Artifact Registry -- Docker repo ----
Write-Host "`n==> Creating Artifact Registry repo: elec-forecast"
gcloud artifacts repositories describe elec-forecast `
    --location $Region --project $ProjectId 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    gcloud artifacts repositories create elec-forecast `
        --repository-format docker `
        --location $Region `
        --description "elec-forecast Docker images" `
        --project $ProjectId
} else {
    Write-Host "    Already exists -- skipping creation."
}
gcloud auth configure-docker "$Region-docker.pkg.dev" --quiet

# ---- 5 & 6. BigQuery datasets + tables (via Python client) ----
# Note: the bq CLI has a known absl-py conflict on some Windows installs;
# using the Python client directly avoids it.
Write-Host "`n==> Creating BigQuery datasets and tables..."
$BqScript = @"
from google.cloud import bigquery
from pathlib import Path
import sys

client = bigquery.Client(project='$ProjectId')
region = '$Region'

datasets = ['elec_raw', 'elec_features', 'elec_ml']
for ds_id in datasets:
    ref = bigquery.DatasetReference('$ProjectId', ds_id)
    try:
        client.get_dataset(ref)
        print(f'    Already exists: {ds_id}')
    except Exception:
        ds = bigquery.Dataset(ref)
        ds.location = region
        client.create_dataset(ds)
        print(f'    Created: {ds_id}')

ddl_files = [
    'infra/sql/ddl/elec_raw.sql',
    'infra/sql/ddl/elec_features.sql',
    'infra/sql/ddl/elec_ml.sql',
]
for f in ddl_files:
    sql = Path(f).read_text(encoding='utf-8')
    print(f'    Applying {f}')
    job = client.query(sql)
    job.result()
    print(f'    OK')
"@
.venv\Scripts\python -c $BqScript

# ---- 7. Secret Manager ----
Write-Host "`n==> Creating Secret Manager secrets..."

function Set-GcpSecret {
    param([string]$Name, [string]$Value)
    gcloud secrets describe $Name --project $ProjectId 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $Value | gcloud secrets create $Name --data-file=- --project $ProjectId
        Write-Host "    Created: $Name"
    } else {
        $Value | gcloud secrets versions add $Name --data-file=- --project $ProjectId
        Write-Host "    Updated: $Name"
    }
}

Set-GcpSecret -Name "GCP_PROJECT_ID" -Value $ProjectId
Set-GcpSecret -Name "GCS_BUCKET"     -Value $Bucket

# ---- Summary ----
Write-Host "`n====================================================="
Write-Host " Setup complete. Update your .env with:"
Write-Host "====================================================="
Write-Host " GCP_PROJECT_ID=$ProjectId"
Write-Host " GCS_BUCKET=$Bucket"
Write-Host ""
Write-Host " Next steps:"
Write-Host "   1. .\infra\cloudrun\deploy.ps1   -- build + deploy Cloud Run Jobs"
Write-Host "   2. .\infra\scheduler\setup.ps1   -- create Cloud Scheduler triggers"
Write-Host "   3. Connect Cloud Build to GitHub via GCP Console (UI only)"
Write-Host "====================================================="
