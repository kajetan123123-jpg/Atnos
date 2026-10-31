# Migrates large tool caches created while working on Atnos from C: to F:.
# Run this only after closing Codex, Atnos Desktop, terminals, dev servers, and browsers used by Playwright.

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$targets = @(
    @{
        Source = "$env:USERPROFILE\.cache\codex-runtimes"
        Target = "E:\Atnos\App\data\tool-caches\codex-runtimes"
        Kind = "junction"
        Note = "Codex bundled runtimes. Close Codex before applying."
    },
    @{
        Source = "$env:USERPROFILE\.codex\sessions"
        Target = "E:\Atnos\App\data\tool-caches\codex\sessions"
        Kind = "junction"
        Note = "Codex session logs. Close Codex before applying."
    },
    @{
        Source = "$env:USERPROFILE\.codex\archived_sessions"
        Target = "E:\Atnos\App\data\tool-caches\codex\archived_sessions"
        Kind = "junction"
        Note = "Codex archived sessions."
    },
    @{
        Source = "$env:USERPROFILE\.codex\.tmp"
        Target = "E:\Atnos\App\data\tool-caches\codex\tmp"
        Kind = "junction"
        Note = "Codex temporary files."
    },
    @{
        Source = "$env:LOCALAPPDATA\npm-cache"
        Target = "E:\Atnos\App\data\tool-caches\npm-cache"
        Kind = "junction"
        Note = "npm cache. Safe to regenerate, but junction avoids future C growth."
    },
    @{
        Source = "$env:LOCALAPPDATA\pip\Cache"
        Target = "E:\Atnos\App\data\tool-caches\pip-cache"
        Kind = "junction"
        Note = "pip cache. Safe to regenerate, but junction avoids future C growth."
    },
    @{
        Source = "$env:LOCALAPPDATA\ms-playwright"
        Target = "E:\Atnos\App\data\tool-caches\ms-playwright"
        Kind = "junction"
        Note = "Playwright browsers. Reinstall if a tool ignores the junction."
    }
)

function Get-PathSizeBytes {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return 0
    }
    $sum = Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { -not $_.PSIsContainer } |
        Measure-Object -Property Length -Sum
    if ($null -eq $sum.Sum) {
        return 0
    }
    return [int64]$sum.Sum
}

function Test-IsJunction {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Get-DirectoryHasChildren {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    return [bool](Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Move-DirectoryTreeIntoTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )

    New-Item -ItemType Directory -Force -Path $Target | Out-Null

    $robocopyArgs = @(
        $Source,
        $Target,
        "/E",
        "/MOVE",
        "/R:1",
        "/W:1",
        "/NFL",
        "/NDL",
        "/NP",
        "/NJH",
        "/NJS"
    )
    & robocopy @robocopyArgs | Out-Null
    $code = $LASTEXITCODE

    # Robocopy exit codes 0-7 are success/non-fatal. 8+ means at least one failure.
    if ($code -ge 8) {
        Write-Warning "Robocopy reported failures while moving $Source to $Target. Exit code: $code"
    }
}

function Ensure-JunctionWhenSourceEmpty {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        New-Item -ItemType Junction -Path $Source -Target $Target | Out-Null
        return $true
    }

    if (Get-DirectoryHasChildren -Path $Source) {
        Write-Warning "Source still has files and cannot become a junction yet: $Source"
        Write-Warning "Close Codex/Atnos/dev servers and rerun this script with -Apply."
        return $false
    }

    Remove-Item -LiteralPath $Source -Force
    New-Item -ItemType Junction -Path $Source -Target $Target | Out-Null
    return $true
}

Write-Host "Atnos cache migration C: -> F:" -ForegroundColor Cyan
Write-Host "Mode: $(if ($Apply) { 'APPLY' } else { 'DRY RUN' })"
Write-Host ""

foreach ($entry in $targets) {
    $source = $entry.Source
    $target = $entry.Target
    $sizeBytes = Get-PathSizeBytes -Path $source
    $sizeGb = [math]::Round($sizeBytes / 1GB, 2)
    $exists = Test-Path -LiteralPath $source
    $alreadyJunction = Test-IsJunction -Path $source

    [pscustomobject]@{
        Source = $source
        Target = $target
        Exists = $exists
        AlreadyJunction = $alreadyJunction
        SizeGB = $sizeGb
        Note = $entry.Note
    } | Format-List

    if (-not $Apply -or -not $exists -or $alreadyJunction) {
        continue
    }

    $targetParent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $targetParent | Out-Null

    if ($PSCmdlet.ShouldProcess($source, "Move to $target and create junction")) {
        if (Test-Path -LiteralPath $target) {
            Write-Host "Target already exists; continuing partial migration: $target" -ForegroundColor Yellow
        }

        Move-DirectoryTreeIntoTarget -Source $source -Target $target
        $junctionCreated = Ensure-JunctionWhenSourceEmpty -Source $source -Target $target
        if ($junctionCreated) {
            Write-Host "Junction ready: $source -> $target" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "After migration, verify:" -ForegroundColor Cyan
Write-Host "Get-PSDrive -Name C,F"
Write-Host "Test-Path E:\Atnos\App\data\tool-caches"
