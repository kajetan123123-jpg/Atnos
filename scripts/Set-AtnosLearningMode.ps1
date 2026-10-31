[CmdletBinding()]
param(
    [ValidateSet("Enable", "Restore")]
    [string]$Mode = "Enable",

    [string]$ConfigPath = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\config\.env",

    [string]$StatePath = "E:\Atnos\Storage\learning-mode\previous-flags.json"
)

$ErrorActionPreference = "Stop"

$safeFlags = [ordered]@{
    LEARNING_MODE                       = "true"
    YOUTUBE_WRITE_BLOCK                 = "true"
    YOUTUBE_UPLOAD_ENABLED              = "false"
    YOUTUBE_EDIT_ENABLED                = "false"
    YOUTUBE_DELETE_ENABLED              = "false"
    YOUTUBE_COMMENT_ENABLED             = "false"
    YOUTUBE_PLAYLIST_WRITE_ENABLED      = "false"
    YOUTUBE_SCOPE_ESCALATION_ALLOWED    = "false"
    EXTERNAL_AI_TRANSFER_ALLOWED        = "false"
    CLOUD_LLM_ALLOWED                   = "false"
    LOCAL_PROCESSING_ONLY               = "true"
}

function Read-EnvFile([string]$Path) {
    $result = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $result
    }

    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)$') {
            $result[$matches[1]] = $matches[2]
        }
    }
    return $result
}

function Update-EnvFile([string]$Path, [System.Collections.IDictionary]$Updates) {
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $Path))
    if (-not $parent.StartsWith("E:\Atnos\Profiles\AtnosSystem\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to edit a runtime config outside E:\Atnos\Profiles\AtnosSystem."
    }
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null

    $lines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $Path) {
        $lines.AddRange([string[]][System.IO.File]::ReadAllLines($Path))
    }

    foreach ($entry in $Updates.GetEnumerator()) {
        $pattern = '^\s*' + [regex]::Escape([string]$entry.Key) + '\s*='
        $found = $false
        for ($index = 0; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -match $pattern) {
                $lines[$index] = "$($entry.Key)=$($entry.Value)"
                $found = $true
                break
            }
        }
        if (-not $found) {
            $lines.Add("$($entry.Key)=$($entry.Value)")
        }
    }

    if (Test-Path -LiteralPath $Path) {
        $replaceBackup = Join-Path $parent ".env.learning-mode.write-backup"
        [System.IO.File]::Copy($Path, $replaceBackup, $true)
        try {
            [System.IO.File]::WriteAllLines($Path, $lines, [System.Text.UTF8Encoding]::new($false))
            [System.IO.File]::Delete($replaceBackup)
        } catch {
            [System.IO.File]::Copy($replaceBackup, $Path, $true)
            throw
        }
    } else {
        [System.IO.File]::WriteAllLines($Path, $lines, [System.Text.UTF8Encoding]::new($false))
    }
}

if ($Mode -eq "Enable") {
    $current = Read-EnvFile $ConfigPath
    $previous = [ordered]@{}
    foreach ($key in $safeFlags.Keys) {
        $previous[$key] = if ($current.Contains($key)) { $current[$key] } else { $null }
    }

    $stateParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $StatePath))
    if (-not $stateParent.StartsWith("E:\Atnos\Storage\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to store Learning Mode state outside E:\Atnos\Storage."
    }
    [System.IO.Directory]::CreateDirectory($stateParent) | Out-Null
    $previous | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
    Update-EnvFile $ConfigPath $safeFlags
    Write-Host "Atnos Learning Mode flags enabled. No secrets were read or printed."
    exit 0
}

if (-not (Test-Path -LiteralPath $StatePath)) {
    throw "No previous Learning Mode state exists at $StatePath."
}
$restoreObject = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
$restore = [ordered]@{}
foreach ($property in $restoreObject.PSObject.Properties) {
    $restore[$property.Name] = $property.Value
}
$updates = [ordered]@{}
foreach ($key in $safeFlags.Keys) {
    $value = $restore[$key]
    $updates[$key] = if ($null -eq $value) { "false" } else { [string]$value }
}
Update-EnvFile $ConfigPath $updates
Write-Host "Previous Atnos runtime flags restored."
