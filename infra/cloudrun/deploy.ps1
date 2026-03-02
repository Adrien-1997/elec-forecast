# deploy.ps1 — build image and deploy/update all Cloud Run Jobs
# Run from repo root: .\infra\cloudrun\deploy.ps1
# Prerequisites: gcloud auth login, gcloud config set project <PROJECT_ID>

param(
    [string]$ProjectId = (gcloud config get-value project),
    [string]$Region    = "europe-west9",
    [string]$Repo      = "elec-forecast",
    [string]$Tag       = (git rev-parse --short HEAD)
)

$ImageBase = "$Region-docker.pkg.dev/$ProjectId/$Repo/elec-jobs"
$Image     = "${ImageBase}:${Tag}"

Write-Host "==> Building image: $Image"
docker build -t $Image -f infra/cloudrun/Dockerfile.jobs .
docker push $Image

$Jobs = @("ingest", "features", "train", "score")

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

Write-Host "==> Done."
