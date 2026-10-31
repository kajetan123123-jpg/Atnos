# Comfy runtime reliability stress: STOP → START → HEALTH → first generation × N
param(
    [int]$Cycles = 10,
    [int]$Port = 8188,
    [switch]$WithWarmup
)

$ErrorActionPreference = "Continue"
$appRoot = "E:\Atnos\App"
$startScript = Join-Path $appRoot "Start-ComfyUI.ps1"
$stopScript = Join-Path $appRoot "Stop-ComfyUI.ps1"
$outDir = "E:\Atnos\Storage\Data\comfy-reliability"
$reportJson = Join-Path $outDir "stress_results.json"
$errBodies = Join-Path $outDir "http400_bodies"
New-Item -ItemType Directory -Force -Path $outDir, $errBodies | Out-Null

$ckpt = "sd_xl_turbo_1.0_fp16.safetensors"

function Get-PortOwnerSummary {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $list = @()
    foreach ($c in $conns) {
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
        $cmd = [string]$cim.CommandLine
        $managed = ($cmd -match "disable-all-custom-nodes" -and $cmd -match "extra-model-paths-config")
        $list += [pscustomobject]@{
            pid = $c.OwningProcess
            managed = [bool]$managed
            argv_snip = if ($cmd.Length -gt 160) { $cmd.Substring(0, 160) } else { $cmd }
        }
    }
    return $list
}

function Test-FullHealth {
    try {
        $s = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/system_stats" -TimeoutSec 5
        if ($s.StatusCode -ne 200) { return @{ ok = $false; reason = "system_stats" } }
        $required = @("CheckpointLoaderSimple", "KSampler", "LoadImage", "VAEEncode", "EmptyLatentImage")
        foreach ($name in $required) {
            $n = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/object_info/$name" -TimeoutSec 10
            if ($n.StatusCode -ne 200 -or $n.Content -notmatch [regex]::Escape($name)) {
                return @{ ok = $false; reason = "missing_$name" }
            }
        }
        $ckptInfo = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/object_info/CheckpointLoaderSimple" -TimeoutSec 10
        if ($ckptInfo.Content -notlike "*$ckpt*") {
            return @{ ok = $false; reason = "missing_checkpoint:$ckpt" }
        }
        $q = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/queue" -TimeoutSec 5
        if ($q.StatusCode -ne 200) { return @{ ok = $false; reason = "queue" } }
        $owners = @(Get-PortOwnerSummary)
        $managed = ($owners | Where-Object { $_.managed } | Measure-Object).Count -gt 0
        if (-not $managed) {
            return @{ ok = $false; reason = "port_not_managed" }
        }
        return @{ ok = $true; reason = "ok" }
    } catch {
        return @{ ok = $false; reason = $_.Exception.Message }
    }
}

function Invoke-FirstGeneration {
    param([int]$Cycle)
    # Raw JSON avoids PowerShell ConvertTo-Json hangs on nested hashtables.
    $seed = 1000 + $Cycle
    $json = @"
{"prompt":{"1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"$ckpt"}},"2":{"class_type":"CLIPTextEncode","inputs":{"text":"reliability test cycle $Cycle","clip":["1",1]}},"3":{"class_type":"CLIPTextEncode","inputs":{"text":"","clip":["1",1]}},"4":{"class_type":"EmptyLatentImage","inputs":{"width":64,"height":64,"batch_size":1}},"5":{"class_type":"KSampler","inputs":{"seed":$seed,"steps":1,"cfg":1.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["1",0],"positive":["2",0],"negative":["3",0],"latent_image":["4",0]}},"6":{"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},"7":{"class_type":"SaveImage","inputs":{"filename_prefix":"atnos_reliability_c$Cycle","images":["6",0]}}},"client_id":"atnos-reliability-$Cycle"}
"@
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$Port/prompt" `
            -ContentType "application/json" -Body $json -TimeoutSec 60
        return @{
            status = [int]$resp.StatusCode
            ok = ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300)
            body = $resp.Content
        }
    } catch {
        $status = 0
        $body = $_.Exception.Message
        try {
            if ($_.Exception.Response) {
                $status = [int]$_.Exception.Response.StatusCode
            }
        } catch {}
        try {
            if ($_.ErrorDetails.Message) { $body = $_.ErrorDetails.Message }
        } catch {}
        $path = Join-Path $errBodies ("cycle_{0}_status_{1}.txt" -f $Cycle, $status)
        Set-Content -LiteralPath $path -Value $body -Encoding UTF8
        return @{ status = $status; ok = $false; body = $body; saved = $path }
    }
}

$results = @()
Write-Output "=== COMFY RELIABILITY STRESS cycles=$Cycles warmup=$WithWarmup ==="

for ($i = 1; $i -le $Cycles; $i++) {
    Write-Output ""
    Write-Output "----- CYCLE $i / $Cycles -----"
    $cycleStart = Get-Date

    # STOP
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript -Port $Port 2>&1 | ForEach-Object { Write-Output $_ }
    $stopOk = ((Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0)

    # START
    $startArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript, "-Port", "$Port", "-ReadyTimeoutSec", "180")
    if ($WithWarmup) { $startArgs += "-Warmup" }
    $t0 = Get-Date
    & powershell @startArgs 2>&1 | ForEach-Object { Write-Output $_ }
    $startExit = $LASTEXITCODE
    $startupSec = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)

    # HEALTH
    $t1 = Get-Date
    $health = Test-FullHealth
    $healthSec = [math]::Round(((Get-Date) - $t1).TotalSeconds, 1)
    $owners = @(Get-PortOwnerSummary)

    # FIRST GENERATION (no retry mask)
    $gen = @{ status = 0; ok = $false; body = "skipped" }
    if ($health.ok) {
        $gen = Invoke-FirstGeneration -Cycle $i
    }

    $pass = ($startExit -eq 0) -and $health.ok -and $gen.ok -and (($owners | Where-Object managed).Count -gt 0)
    $row = [ordered]@{
        cycle = $i
        pass = [bool]$pass
        stop_port_clear = [bool]$stopOk
        start_exit = $startExit
        startup_sec = $startupSec
        health_ok = [bool]$health.ok
        health_reason = [string]$health.reason
        health_sec = $healthSec
        first_gen_status = $gen.status
        first_gen_ok = [bool]$gen.ok
        managed_owner = (($owners | Where-Object managed).Count -gt 0)
        owners = $owners
        cycle_sec = [math]::Round(((Get-Date) - $cycleStart).TotalSeconds, 1)
        first_gen_body_snip = if ($gen.body) { $gen.body.Substring(0, [Math]::Min(300, $gen.body.Length)) } else { "" }
    }
    $results += [pscustomobject]$row
    Write-Output ("RESULT cycle={0} pass={1} startup={2}s health={3} first_gen={4} managed={5}" -f `
        $i, $pass, $startupSec, $health.reason, $gen.status, $row.managed_owner)
}

$passCount = @($results | Where-Object { $_.pass }).Count
$startupTimes = @($results | ForEach-Object { $_.startup_sec } | Sort-Object)
$medianStartup = if ($startupTimes.Count -gt 0) {
    $mid = [int][math]::Floor(($startupTimes.Count - 1) / 2)
    if ($startupTimes.Count % 2 -eq 0) {
        [math]::Round(($startupTimes[$mid] + $startupTimes[$mid + 1]) / 2, 1)
    } else {
        $startupTimes[$mid]
    }
} else { 0 }
$firstGenOk = @($results | Where-Object { $_.first_gen_ok }).Count
$desktopRace = (@($results | Where-Object { -not $_.managed_owner }).Count -eq 0)

$summary = [ordered]@{
    cycles = $Cycles
    pass = $passCount
    first_generation_success = "$firstGenOk / $Cycles"
    median_startup_sec = $medianStartup
    desktop_race_resolved = $desktopRace
    with_warmup = [bool]$WithWarmup
    results = $results
}
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportJson -Encoding UTF8

Write-Output ""
Write-Output "=== SUMMARY ==="
Write-Output ("Restart cycles: {0} / {1}" -f $passCount, $Cycles)
Write-Output ("First-generation success: {0}" -f $summary.first_generation_success)
Write-Output ("Median startup: {0}s" -f $medianStartup)
Write-Output ("Desktop race clean: {0}" -f $desktopRace)
Write-Output ("Results: $reportJson")
if ($passCount -eq $Cycles) { exit 0 } else { exit 1 }
