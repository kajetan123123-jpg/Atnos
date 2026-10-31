$ErrorActionPreference = "Stop"

$workflows = @(
    "/atnos-n8n/workflows/atnos-youtube-daily-ops.json",
    "/atnos-n8n/workflows/atnos-youtube-review-queue.json",
    "/atnos-n8n/workflows/atnos-youtube-create-approval.json",
    "/atnos-n8n/workflows/atnos-youtube-analytics-sync.json"
)

foreach ($workflow in $workflows) {
    Write-Host "Importing $workflow"
    docker exec atnos-n8n n8n import:workflow --input=$workflow
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to import workflow: $workflow"
    }
}

Write-Host "Imported n8n workflow templates. Open http://127.0.0.1:5678 and activate only after checking credentials and approval policy."
