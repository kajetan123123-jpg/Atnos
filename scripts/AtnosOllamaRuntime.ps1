# Resolve the canonical application's configured Ollama endpoint without loading
# credentials from .env into the shell or changing another application's process.
function Get-AtnosOllamaModelRoles {
    param([string]$EnvFile)
    # Import only non-secret model names. Never evaluate or dot-source .env.
    $roles = [ordered]@{
        OLLAMA_FAST_MODEL = 'qwen3.5:9b'
        OLLAMA_CHAT_MODEL = 'qwen3.5:9b'
        OLLAMA_MAIN_MODEL = 'qwen3:14b'
        OLLAMA_ORCHESTRATION_MODEL = 'qwen3:14b'
        OLLAMA_DOCUMENT_MODEL = 'qwen3:8b'
        OLLAMA_SYNTHESIS_MODEL = 'qwen3:8b'
        OLLAMA_VISION_MODEL = 'qwen3-vl:8b-instruct'
    }
    if ($EnvFile -and (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
            if ($line -match '^\s*(?:export\s+)?(OLLAMA_[A-Z_]+)\s*=\s*(.*?)\s*$') {
                $key = $Matches[1]
                $rawValue = $Matches[2]
                if (-not $roles.Contains($key)) { continue }
                $value = ($rawValue -replace '\s+#.*$', '').Trim().Trim('"').Trim("'")
                if (-not $value) { continue }
                if ($value -notmatch '^[A-Za-z0-9][A-Za-z0-9_.:/-]*$') {
                    throw "Invalid local model name for $key; no service was changed."
                }
                $roles[$key] = $value
            }
        }
    }
    return $roles
}

function Get-AtnosOllamaEndpoint {
    param([string]$EnvFile)

    $baseUrl = $null
    if ($EnvFile -and (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
            if ($line -match '^\s*OLLAMA_BASE_URL\s*=\s*(.*?)\s*$') {
                $baseUrl = $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    if (-not $baseUrl) { $baseUrl = $env:OLLAMA_BASE_URL }
    if (-not $baseUrl) { $baseUrl = 'http://127.0.0.1:11434' }
    $endpoint = $null
    if (-not [Uri]::TryCreate($baseUrl, [UriKind]::Absolute, [ref]$endpoint) -or
        $endpoint.Scheme -ne 'http' -or -not $endpoint.IsLoopback -or
        $endpoint.UserInfo -or $endpoint.AbsolutePath -ne '/' -or
        $endpoint.Query -or $endpoint.Fragment -or $endpoint.Port -lt 1) {
        throw 'OLLAMA_BASE_URL must be a local HTTP origin; model launcher did not change any process.'
    }
    $hostName = $endpoint.DnsSafeHost
    $serveHost = if ($hostName.Contains(':')) { "[$hostName]:$($endpoint.Port)" } else { "$($hostName):$($endpoint.Port)" }
    return [pscustomobject]@{
        BaseUrl = $endpoint.GetLeftPart([UriPartial]::Authority)
        Host = $serveHost
        Port = $endpoint.Port
    }
}
