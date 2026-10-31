param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [string]$GoogleOAuthClientJson = ""
)

$ErrorActionPreference = "Stop"

function Get-EnvEntries([string]$Path) {
    $entries = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $entries
    }
    foreach ($line in [System.IO.File]::ReadLines($Path)) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            continue
        }
        $key = $matches[1]
        $value = $matches[2].Trim().Trim('"').Trim("'")
        if ($value) {
            $entries[$key] = $value
        }
    }
    return $entries
}

function Test-SkippedPath([string]$Path) {
    return $Path -match '\\(\.git|node_modules|\.venv|dist|account-secrets|browser_profiles|tokens|secrets)\\'
}

function Get-TextFiles([string[]]$Roots, [string]$EnvPath) {
    $extensions = @('.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.md', '.txt', '.log', '.ps1', '.cmd', '.bat')
    $files = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
    foreach ($root in $Roots | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
            if ((Test-SkippedPath $_.FullName) -or $_.FullName -eq $EnvPath) {
                return
            }
            if ($extensions -notcontains $_.Extension.ToLowerInvariant()) {
                return
            }
            if ($_.Length -gt 10MB) {
                return
            }
            $files.Add($_)
        }
    }
    return $files | Sort-Object FullName -Unique
}

$resolvedEnv = [System.IO.Path]::GetFullPath($EnvFile)
$resolvedRepo = [System.IO.Path]::GetFullPath($RepoRoot)
$resolvedRuntime = [System.IO.Path]::GetFullPath($RuntimeRoot)
$entries = Get-EnvEntries $resolvedEnv
if ($GoogleOAuthClientJson -and (Test-Path -LiteralPath $GoogleOAuthClientJson -PathType Leaf)) {
    try {
        $oauth = Get-Content -LiteralPath $GoogleOAuthClientJson -Raw | ConvertFrom-Json
        $payload = if ($oauth.installed) { $oauth.installed } else { $oauth.web }
        if ($payload.client_secret) {
            $entries["GOOGLE_OAUTH_CLIENT_SECRET"] = [string]$payload.client_secret
        }
    } catch {
        throw "Could not parse Google OAuth client JSON for exact-value scanning: $($_.Exception.Message)"
    }
}
$files = @(Get-TextFiles @($resolvedRepo, $resolvedRuntime) $resolvedEnv)

$exactFindings = [System.Collections.Generic.List[object]]::new()
foreach ($entry in $entries.GetEnumerator()) {
    $value = [string]$entry.Value
    if ($value.Length -lt 8) {
        continue
    }
    foreach ($file in $files) {
        $lineNumber = 0
        try {
            foreach ($line in [System.IO.File]::ReadLines($file.FullName)) {
                $lineNumber += 1
                if ($line.Contains($value, [System.StringComparison]::Ordinal)) {
                    $exactFindings.Add([ordered]@{
                        key = $entry.Key
                        path = $file.FullName
                        line = $lineNumber
                    })
                }
            }
        } catch {
            continue
        }
    }
}

$patterns = [ordered]@{
    google_client_secret = 'GOCSPX-[A-Za-z0-9_-]{12,}'
    openai_api_key = 'sk-(?:proj-)?[A-Za-z0-9_-]{20,}'
    generic_secret_assignment = '(?i)(api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password|passwd|cvv)\s*[:=]\s*["'']?[^\s,"'']{8,}'
}
$patternFindings = [System.Collections.Generic.List[object]]::new()
foreach ($file in $files) {
    $lineNumber = 0
    try {
        foreach ($line in [System.IO.File]::ReadLines($file.FullName)) {
            $lineNumber += 1
            foreach ($pattern in $patterns.GetEnumerator()) {
                if ($line -match $pattern.Value) {
                    $patternFindings.Add([ordered]@{
                        pattern = $pattern.Key
                        path = $file.FullName
                        line = $lineNumber
                    })
                }
            }
        }
    } catch {
        continue
    }
}

$report = [ordered]@{
    generated_at_utc = [DateTime]::UtcNow.ToString('o')
    repo_root = $resolvedRepo
    runtime_root = $resolvedRuntime
    env_key_names = @($entries.Keys | Sort-Object)
    scanned_file_count = $files.Count
    git_history_available = Test-Path -LiteralPath (Join-Path $resolvedRepo '.git')
    exact_value_findings = @($exactFindings)
    pattern_findings = @($patternFindings)
    note = 'Values are intentionally omitted. Findings contain key or pattern name, path and line only.'
}

$output = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($output)) | Out-Null
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $output -Encoding UTF8
Write-Output $output
