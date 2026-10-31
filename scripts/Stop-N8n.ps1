$ErrorActionPreference = "Stop"
$composeDir = "E:\Atnos\App\infra\n8n"

Push-Location $composeDir
try {
    docker-compose --env-file .env down
    if ($LASTEXITCODE -ne 0) {
        throw "docker-compose down failed."
    }
} finally {
    Pop-Location
}
