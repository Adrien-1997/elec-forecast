# deploy.ps1 — build images via Cloud Build and deploy all Cloud Run resources
# Run from repo root: .\infra\cloudrun\deploy.ps1
# Prerequisites: gcloud auth login, gcloud config set project <PROJECT_ID>

param(
    [string]$ProjectId = (gcloud config get-value project),
    [string]$Region    = "europe-west9",
    [string]$Repo      = "elec-forecast",
    [string]$Tag       = (git rev-parse --short HEAD)
)

$ImageBase  = "$Region-docker.pkg.dev/$ProjectId/$Repo/elec-jobs"
$Image      = "${ImageBase}:latest"
$DashImage  = "$Region-docker.pkg.dev/$ProjectId/$Repo/elec-dashboard:latest"

# ── Build both images via Cloud Build (no local Docker required) ──────────────

Write-Host "==> Building images via Cloud Build (tag: $Tag)"
gcloud builds submit . `
    --config infra/cloudrun/cloudbuild.yaml `
    --project $ProjectId `
    --substitutions "_REGION=$Region,_REPO=$Repo,SHORT_SHA=$Tag"

# ── Deploy Cloud Run Jobs ─────────────────────────────────────────────────────

$Jobs = @("ingest", "features", "train", "forecast", "metrics")

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
