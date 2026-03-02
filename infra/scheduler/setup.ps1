# setup.ps1 — create Cloud Scheduler jobs that trigger Cloud Run Jobs via HTTP
# Cloud Scheduler is only available in certain regions; europe-west1 is used here.
# Run from repo root: .\infra\scheduler\setup.ps1

param(
    [string]$ProjectId      = (gcloud config get-value project),
    [string]$RunRegion      = "europe-west9",
    [string]$SchedulerRegion = "europe-west1",
    [string]$ServiceAccount = "elec-forecast-sa@$ProjectId.iam.gserviceaccount.com"
)

$Jobs = @(
    @{ Name = "ingest";   Schedule = "*/30 * * * *" },
    @{ Name = "features"; Schedule = "*/30 * * * *" },
    @{ Name = "train";    Schedule = "0 2 * * 0"    },  # Sunday 2am
    @{ Name = "score";    Schedule = "*/30 * * * *" }
)

foreach ($Job in $Jobs) {
    $Name     = $Job.Name
    $Schedule = $Job.Schedule
    $Uri      = "https://$RunRegion-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$ProjectId/jobs/$Name`:run"

    Write-Host "==> Scheduling: $Name  [$Schedule]"
    gcloud scheduler jobs create http "$Name-trigger" `
        --schedule $Schedule `
        --uri $Uri `
        --http-method POST `
        --oauth-service-account-email $ServiceAccount `
        --location $SchedulerRegion `
        --project $ProjectId `
        --time-zone "Europe/Paris"
}

Write-Host "==> Done."
