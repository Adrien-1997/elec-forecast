# deploy.ps1 — build images via Cloud Build and deploy all Cloud Run resources
# Run from repo root: .\infra\cloudrun\deploy.ps1
# Prerequisites: gcloud auth login, gcloud config set project <PROJECT_ID>

param(
    [string]$ProjectId = (gcloud config get-value project),
    [string]$Region    = "europe-west9",
    [string]$Repo      = "elec-forecast",
    [string]$Tag       = (git rev-parse --short HEAD)
)

$ImageBase    = "$Region-docker.pkg.dev/$ProjectId/$Repo/elec-jobs"
$Image        = "${ImageBase}:latest"
$DashImage    = "$Region-docker.pkg.dev/$ProjectId/$Repo/elec-dashboard:latest"
$MlflowImage  = "$Region-docker.pkg.dev/$ProjectId/$Repo/elec-mlflow:latest"

# ── Build both images via Cloud Build (no local Docker required) ──────────────

Write-Host "==> Building images via Cloud Build (tag: $Tag)"
gcloud builds submit . `
    --config infra/cloudrun/cloudbuild.yaml `
    --project $ProjectId `
    --substitutions "_REGION=$Region,_REPO=$Repo,SHORT_SHA=$Tag"

# ── Deploy MLflow UI (Cloud Run Service) ─────────────────────────────────────

Write-Host "==> Deploying Cloud Run Service: elec-mlflow"
gcloud run deploy elec-mlflow `
    --image $MlflowImage `
    --region $Region `
    --project $ProjectId `
    --set-secrets "GCS_BUCKET=GCS_BUCKET:latest" `
    --service-account "elec-forecast-sa@$ProjectId.iam.gserviceaccount.com" `
    --allow-unauthenticated `
    --min-instances 0 `
    --max-instances 1 `
    --memory 512Mi `
    --cpu 1 `
    --port 8080 `
    --timeout 300

$MlflowUrl = (gcloud run services describe elec-mlflow `
    --region $Region --project $ProjectId `
    --format "value(status.url)").Trim()
Write-Host "==> MLflow URL: $MlflowUrl"

# ── Deploy Cloud Run Jobs ─────────────────────────────────────────────────────

$Jobs = @("ingest", "features", "forecast", "metrics")

foreach ($Job in $Jobs) {
    Write-Host "==> Deploying Cloud Run Job: $Job"
    gcloud run jobs deploy $Job `
        --image $Image `
        --region $Region `
        --project $ProjectId `
        --set-env-vars "JOB_MODULE=$Job" `
        --set-secrets "GCP_PROJECT_ID=GCP_PROJECT_ID:latest,GCS_BUCKET=GCS_BUCKET:latest" `
        --service-account "elec-forecast-sa@$ProjectId.iam.gserviceaccount.com" `
        --max-retries 1 `
        --task-timeout 600
}

# train: also needs MLFLOW_TRACKING_URI
Write-Host "==> Deploying Cloud Run Job: train"
gcloud run jobs deploy train `
    --image $Image `
    --region $Region `
    --project $ProjectId `
    --set-env-vars "JOB_MODULE=train,MLFLOW_TRACKING_URI=$MlflowUrl,MLFLOW_EXPERIMENT_NAME=elec-forecast" `
    --set-secrets "GCP_PROJECT_ID=GCP_PROJECT_ID:latest,GCS_BUCKET=GCS_BUCKET:latest" `
    --service-account "elec-forecast-sa@$ProjectId.iam.gserviceaccount.com" `
    --max-retries 1 `
    --task-timeout 600

# backfill: long-running one-shot job, no scheduler trigger.
# Run manually: gcloud run jobs execute backfill --region europe-west9
# With custom range: gcloud run jobs execute backfill --region europe-west9 `
#   --update-env-vars BACKFILL_START_DATE=2024-01-01,BACKFILL_END_DATE=2025-12-31
Write-Host "==> Deploying Cloud Run Job: backfill"
gcloud run jobs deploy backfill `
    --image $Image `
    --region $Region `
    --project $ProjectId `
    --set-env-vars "JOB_MODULE=backfill" `
    --set-secrets "GCP_PROJECT_ID=GCP_PROJECT_ID:latest,GCS_BUCKET=GCS_BUCKET:latest" `
    --service-account "elec-forecast-sa@$ProjectId.iam.gserviceaccount.com" `
    --max-retries 0 `
    --task-timeout 7200

# ── Deploy Dashboard (Cloud Run Service — always-on HTTP) ─────────────────────

Write-Host "==> Deploying Cloud Run Service: elec-dashboard"
gcloud run deploy elec-dashboard `
    --image $DashImage `
    --region $Region `
    --project $ProjectId `
    --set-secrets "GCP_PROJECT_ID=GCP_PROJECT_ID:latest,GCS_BUCKET=GCS_BUCKET:latest" `
    --service-account "elec-forecast-sa@$ProjectId.iam.gserviceaccount.com" `
    --allow-unauthenticated `
    --min-instances 0 `
    --max-instances 1 `
    --memory 512Mi `
    --cpu 1 `
    --port 8080 `
    --timeout 60

Write-Host "==> Done."
