# Frees C: space safely by moving large user downloads to Atnos storage on E:.
# Default mode is dry-run. Nothing is moved unless -Apply is provided.

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Apply,
    [switch]$MoveLargeDownloads,
    [switch]$DisableHibernate,
    [switch]$EmptyRecycleBin,
    [int]$MinimumDownloadSizeMB = 500,
    [string]$ArchiveRoot = "E:\Atnos\Storage\UserArchive\DownloadsFromC"
)

$ErrorActionPreference = "Stop"

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

function Get-LargeDownloadCandidates {
    $downloads = Join-Path $env:USERPROFILE "Downloads"
    if (-not (Test-Path -LiteralPath $downloads)) {
        return @()
    }

    $minBytes = [int64]$MinimumDownloadSizeMB * 1MB
    return Get-ChildItem -LiteralPath $downloads -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            $size = if ($_.PSIsContainer) { Get-PathSizeBytes -Path $_.FullName } else { [int64]$_.Length }
            if ($size -ge $minBytes) {
                [pscustomobject]@{
                    Name = $_.Name
                    Source = $_.FullName
                    SizeGB = [math]::Round($size / 1GB, 2)
                    IsDirectory = $_.PSIsContainer
                    LastWriteTime = $_.LastWriteTime
                }
            }
        } |
        Sort-Object SizeGB -Descending
}

function Move-LargeDownloads {
    $candidates = @(Get-LargeDownloadCandidates)
    if ($candidates.Count -eq 0) {
        Write-Host "No Downloads candidates >= $MinimumDownloadSizeMB MB."
        return
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $archive = Join-Path $ArchiveRoot $stamp
    $manifestPath = Join-Path $archive "manifest.csv"

    Write-Host "Large Downloads candidates:" -ForegroundColor Cyan
    $candidates | Format-Table Name, SizeGB, Source -AutoSize
    $totalGb = [math]::Round((($candidates | Measure-Object -Property SizeGB -Sum).Sum), 2)
    Write-Host "Estimated movable space: $totalGb GB"

    if (-not $Apply) {
        Write-Host "Dry-run only. Add -Apply to move these files to: $archive" -ForegroundColor Yellow
        return
    }

    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $candidates | Export-Csv -NoTypeInformation -Encoding UTF8 -Path $manifestPath

    foreach ($candidate in $candidates) {
        $destination = Join-Path $archive $candidate.Name
        if ($PSCmdlet.ShouldProcess($candidate.Source, "Move to $destination")) {
            Move-Item -LiteralPath $candidate.Source -Destination $destination -Force
        }
    }

    Write-Host "Moved large Downloads to: $archive" -ForegroundColor Green
    Write-Host "Manifest: $manifestPath"
}

function Disable-Hibernation {
    Write-Host "hiberfil.sys can be removed by disabling hibernation." -ForegroundColor Cyan
    if (-not $Apply) {
        Write-Host "Dry-run only. Add -Apply -DisableHibernate to run: powercfg /hibernate off" -ForegroundColor Yellow
        return
    }
    if ($PSCmdlet.ShouldProcess("Windows hibernation", "Disable")) {
        powercfg /hibernate off
        Write-Host "Hibernation disabled. hiberfil.sys should be removed by Windows." -ForegroundColor Green
    }
}

function Clear-RecycleBinSafe {
    if (-not $Apply) {
        Write-Host "Dry-run only. Add -Apply -EmptyRecycleBin to empty recycle bin." -ForegroundColor Yellow
        return
    }
    if ($PSCmdlet.ShouldProcess("Recycle Bin", "Empty")) {
        Clear-RecycleBin -Force -ErrorAction Stop
        Write-Host "Recycle Bin emptied." -ForegroundColor Green
    }
}

Write-Host "C: cleanup helper" -ForegroundColor Cyan
Write-Host "Mode: $(if ($Apply) { 'APPLY' } else { 'DRY RUN' })"
Write-Host ""

Get-PSDrive -Name C,E |
    Select-Object Name,@{Name='UsedGB';Expression={[math]::Round($_.Used/1GB,2)}},@{Name='FreeGB';Expression={[math]::Round($_.Free/1GB,2)}} |
    Format-Table -AutoSize

if (-not $MoveLargeDownloads -and -not $DisableHibernate -and -not $EmptyRecycleBin) {
    Write-Host ""
    Write-Host "No action selected. Useful dry-runs:" -ForegroundColor Yellow
    Write-Host "E:\Atnos\App\scripts\free_c_drive_space.ps1 -MoveLargeDownloads"
    Write-Host "E:\Atnos\App\scripts\free_c_drive_space.ps1 -DisableHibernate"
    Write-Host ""
    Write-Host "Useful apply commands:" -ForegroundColor Yellow
    Write-Host "E:\Atnos\App\scripts\free_c_drive_space.ps1 -Apply -MoveLargeDownloads"
    Write-Host "E:\Atnos\App\scripts\free_c_drive_space.ps1 -Apply -DisableHibernate"
    return
}

if ($MoveLargeDownloads) {
    Move-LargeDownloads
}

if ($DisableHibernate) {
    Disable-Hibernation
}

if ($EmptyRecycleBin) {
    Clear-RecycleBinSafe
}

Write-Host ""
Get-PSDrive -Name C,E |
    Select-Object Name,@{Name='UsedGB';Expression={[math]::Round($_.Used/1GB,2)}},@{Name='FreeGB';Expression={[math]::Round($_.Free/1GB,2)}} |
    Format-Table -AutoSize
