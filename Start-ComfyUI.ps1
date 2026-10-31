param(
    [int]$Port = 8188,
    [switch]$PortableFallback,
    [switch]$AllowDesktopGui,
    [switch]$SkipHealthVerify,
    [switch]$Warmup,
    [int]$ReadyTimeoutSec = 120
)

$ErrorActionPreference = "Stop"

$desktopExe = "E:\Atnos\Tools\ComfyUI-Desktop-App\Comfy Desktop\Comfy Desktop.exe"
$desktopInstallDir = "E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Installs\Atnos"
$managedComfyDir = Join-Path $desktopInstallDir "ComfyUI"
$managedPythonVenv = Join-Path $managedComfyDir ".venv\Scripts\python.exe"
$managedPythonStandalone = Join-Path $desktopInstallDir "standalone-env\python.exe"
$managedMain = Join-Path $managedComfyDir "main.py"
$portableInstallDir = "E:\Atnos\Storage\Legacy\ComfyUI_windows_portable"
$portablePython = Join-Path $portableInstallDir "python_embeded\python.exe"
$portableMain = Join-Path $portableInstallDir "ComfyUI\main.py"
$sharedModelPaths = "E:\Atnos\App\config\comfy-shared-model-paths.yaml"
$customNodePolicy = "E:\Atnos\App\config\comfy_custom_node_policy.json"
$sharedInputDir = "E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Shared\input"
$sharedOutputDir = "E:\Atnos\Storage\Data\comfy-output"
$logDir = "E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\runtime"
$managedStdoutLog = Join-Path $logDir "comfyui-managed.out.log"
$managedStderrLog = Join-Path $logDir "comfyui-managed.err.log"
$startupLog = Join-Path $logDir "comfyui-startup.log"
$markerPath = Join-Path $logDir "comfyui-managed.instance.json"
$requiredCheckpoint = "sd_xl_turbo_1.0_fp16.safetensors"

function Write-StartupLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "o"), $Message
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Add-Content -LiteralPath $startupLog -Value $line -Encoding UTF8
    Write-Output $Message
}

function Get-PortOwners {
    param([int]$ListenPort)
    $rows = @()
    $conns = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $procId = [int]$c.OwningProcess
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        $cmd = [string]($cim.CommandLine)
        $exe = [string]($proc.Path)
        $parent = if ($cim) { [int]$cim.ParentProcessId } else { 0 }
        $kind = "unknown"
        $managed = $false
        if ($cmd -match "disable-all-custom-nodes" -and $cmd -match "extra-model-paths-config" -and $cmd -match "ComfyUI") {
            $kind = "managed_comfy"
            $managed = $true
        }
        elseif ($exe -match "ComfyUI-Installs\\Atnos" -or $cmd -match "ComfyUI-Installs\\Atnos") {
            $kind = "atnos_comfy_unmanaged"
        }
        elseif ($exe -match "Comfy Desktop" -or $proc.ProcessName -eq "Comfy Desktop") {
            $kind = "comfy_desktop_gui"
        }
        elseif ($cmd -match "main\.py" -and ($cmd -match "8188" -or $cmd -match "ComfyUI")) {
            $kind = "comfy_python_other"
        }
        elseif ($proc.ProcessName -match "python") {
            $kind = "python_other"
        }
        $rows += [pscustomobject]@{
            Pid = $procId
            ParentPid = $parent
            Name = $proc.ProcessName
            Executable = $exe
            CommandLine = $cmd
            Kind = $kind
            Managed = $managed
        }
    }
    return $rows
}

function Write-InstanceMarker {
    param(
        [int]$ProcessId,
        [string]$Executable,
        [string]$Argv,
        [string]$InstanceId,
        [string]$Status
    )
    $taskProcessEpoch = $null
    try { $taskProcessEpoch = ([DateTimeOffset](Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime()).ToUnixTimeMilliseconds() / 1000.0 } catch { }
    $payload = [ordered]@{
        instance_id = $InstanceId
        pid = $ProcessId
        process_started_at_epoch = $taskProcessEpoch
        executable = $Executable
        argv = $Argv
        port = $Port
        status = $Status
        policy = "CORE_ONLY"
        marker = "atnos-managed-comfy"
        updated_at = (Get-Date).ToString("o")
    }
    ($payload | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $markerPath -Encoding UTF8
}

function Test-HttpOk {
    param([string]$Url, [int]$TimeoutSec = 4)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSec
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-ComfyFullReady {
    param([switch]$RequireCheckpoint)
    try {
        $stats = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/system_stats" -TimeoutSec 5
        if ($stats.StatusCode -ne 200) { return $false }
        # Selective /object_info/{node} - never parse full object_info (multi-MB ConvertFrom-Json hang)
        $required = @(
            "CheckpointLoaderSimple", "CLIPTextEncode", "KSampler", "VAEDecode",
            "SaveImage", "LoadImage", "VAEEncode", "ImageScale", "EmptyLatentImage"
        )
        foreach ($name in $required) {
            $nodeResp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/object_info/$name" -TimeoutSec 10
            if ($nodeResp.StatusCode -ne 200) {
                Write-StartupLog "Readiness missing core node: $name"
                return $false
            }
            if ($nodeResp.Content -notmatch [regex]::Escape($name)) {
                Write-StartupLog "Readiness empty/invalid object_info for: $name"
                return $false
            }
        }
        if ($RequireCheckpoint) {
            $ckptResp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/object_info/CheckpointLoaderSimple" -TimeoutSec 10
            if ($ckptResp.Content -notlike "*$requiredCheckpoint*") {
                Write-StartupLog "Readiness: required checkpoint missing: $requiredCheckpoint"
                return $false
            }
        }
        $queue = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/queue" -TimeoutSec 5
        if ($queue.StatusCode -ne 200) { return $false }
        return $true
    } catch {
        Write-StartupLog ("Readiness probe error: " + $_.Exception.Message)
        return $false
    }
}

function Stop-KnownComfyOwner {
    param([object]$Owner)
    [void](Write-StartupLog ("Existing port owner preserved PID={0}; startup never restarts a model or GUI." -f $Owner.Pid))
    return $false
}

function Invoke-ComfyWarmup {
    Write-StartupLog "WARMUP: queueing minimal EmptyLatent+checkpoint graph"
    $body = @"
{"prompt":{"1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"$requiredCheckpoint"}},"2":{"class_type":"CLIPTextEncode","inputs":{"text":"warmup","clip":["1",1]}},"3":{"class_type":"CLIPTextEncode","inputs":{"text":"","clip":["1",1]}},"4":{"class_type":"EmptyLatentImage","inputs":{"width":64,"height":64,"batch_size":1}},"5":{"class_type":"KSampler","inputs":{"seed":1,"steps":1,"cfg":1.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["1",0],"positive":["2",0],"negative":["3",0],"latent_image":["4",0]}},"6":{"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},"7":{"class_type":"SaveImage","inputs":{"filename_prefix":"atnos_warmup","images":["6",0]}}},"client_id":"atnos-warmup"}
"@
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$Port/prompt" `
            -ContentType "application/json" -Body $body -TimeoutSec 30
        Write-StartupLog ("WARMUP accepted status={0}" -f $resp.StatusCode)
        return $true
    } catch {
        $detail = $_.Exception.Message
        try {
            $detail = $_.ErrorDetails.Message
        } catch {}
        Write-StartupLog ("WARMUP FAILED: " + $detail)
        return $false
    }
}

# -------- main --------
$started = Get-Date
$instanceId = [guid]::NewGuid().ToString("N").Substring(0, 12)
Write-StartupLog "Start-ComfyUI.ps1 begin port=$Port mode=MANAGED_ONLY policy=CORE_ONLY instance=$instanceId"

New-Item -ItemType Directory -Force -Path $logDir, $sharedInputDir, $sharedOutputDir | Out-Null

$owners = @(Get-PortOwners -ListenPort $Port)
if ($owners.Count -gt 0) {
    foreach ($o in $owners) {
        Write-StartupLog ("Port owner PID={0} parent={1} kind={2} managed={3} exe={4}" -f $o.Pid, $o.ParentPid, $o.Kind, $o.Managed, $o.Executable)
        if ($o.CommandLine) {
            Write-StartupLog ("  argv: " + $o.CommandLine.Substring(0, [Math]::Min(240, $o.CommandLine.Length)))
        }
    }
    $managedOwner = $owners | Where-Object { $_.Managed } | Select-Object -First 1
    if ($managedOwner -and (Test-ComfyFullReady -RequireCheckpoint)) {
        Write-InstanceMarker -ProcessId $managedOwner.Pid -Executable $managedOwner.Executable `
            -Argv $managedOwner.CommandLine -InstanceId $instanceId -Status "READY_EXISTING"
        Write-StartupLog "Managed Comfy already READY on port $Port."
        if ($Warmup) { [void](Invoke-ComfyWarmup) }
        exit 0
    }
    # Wrong or incomplete owner: stop only known Comfy kinds
    foreach ($o in $owners) {
        if (-not $o.Managed) {
            if (-not (Stop-KnownComfyOwner -Owner $o)) {
                Write-StartupLog "ERROR_STATUS=PORT_OCCUPIED_BY_UNKNOWN"
                exit 5
            }
        } else {
            # Managed but not fully ready - recycle
            [void](Stop-KnownComfyOwner -Owner $o)
        }
    }
    Start-Sleep -Milliseconds 800
    $still = @(Get-PortOwners -ListenPort $Port)
    if ($still.Count -gt 0) {
        Write-StartupLog "ERROR_STATUS=PORT_STILL_OCCUPIED_AFTER_STOP"
        exit 6
    }
}

if ($AllowDesktopGui) {
    Write-StartupLog "AllowDesktopGui set - launching GUI (non-canonical)."
    if (-not (Get-Process -Name "Comfy Desktop" -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $desktopExe -WorkingDirectory (Split-Path -Parent $desktopExe)
    }
}

# Prefer .venv (has torch/ROCm). standalone-env is Desktop base interpreter without site packages.
$managedPython = $null
if (Test-Path -LiteralPath $managedPythonVenv) {
    $managedPython = $managedPythonVenv
} elseif (Test-Path -LiteralPath $managedPythonStandalone) {
    $managedPython = $managedPythonStandalone
}

if (-not $managedPython -or -not (Test-Path -LiteralPath $managedMain) -or -not (Test-Path -LiteralPath $sharedModelPaths)) {
    Write-StartupLog "ERROR_STATUS=MANAGED_INSTALL_INCOMPLETE"
    throw "Managed Comfy install incomplete."
}

if (Test-Path -LiteralPath $customNodePolicy) {
    Write-StartupLog "Custom node policy: $customNodePolicy"
}

$argList = @(
    "-s", "main.py",
    "--listen", "127.0.0.1",
    "--port", "$Port",
    "--lowvram",
    "--disable-async-offload",
    "--disable-dynamic-vram",
    "--disable-mmap",
    "--reserve-vram", "1.0",
    "--cache-classic",
    "--disable-all-custom-nodes",
    "--extra-model-paths-config", $sharedModelPaths,
    "--input-directory", $sharedInputDir,
    "--output-directory", $sharedOutputDir
)
$argvJoined = ($argList -join " ")
Write-StartupLog "Starting managed python: $managedPython"
Write-StartupLog "argv: $argvJoined"

# Fully detached create (no stdio inheritance). Parent capture_output callers must not hang.
$quotedArgs = ($argList | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
}) -join ' '
# cmd redirection so stdout/stderr still land on E:
$cmdLine = 'cmd.exe /c ""{0}" {1} >>"{2}" 2>>"{3}""' -f $managedPython, $quotedArgs, $managedStdoutLog, $managedStderrLog
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $cmdLine
    CurrentDirectory = $managedComfyDir
}
if (-not $created -or [int]$created.ReturnValue -ne 0) {
    Write-StartupLog ("ERROR_STATUS=LAUNCH_FAILED return={0}" -f $(if ($created) { $created.ReturnValue } else { 'null' }))
    exit 10
}
$launcherPid = [int]$created.ProcessId
Write-InstanceMarker -ProcessId $launcherPid -Executable $managedPython -Argv $argvJoined `
    -InstanceId $instanceId -Status "STARTING"

# Resolve python listener pid for crash detection (cmd -> python)
Start-Sleep -Milliseconds 400
$proc = Get-Process -Id $launcherPid -ErrorAction SilentlyContinue
if (-not $proc) {
    # cmd may have exited after spawning python; find managed listener/python
    $proc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'disable-all-custom-nodes' -and $_.CommandLine -match "$Port" } |
        Select-Object -First 1 |
        ForEach-Object { Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
}

$deadline = (Get-Date).AddSeconds($ReadyTimeoutSec)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $ownersNow = @(Get-PortOwners -ListenPort $Port)
    $managedAlive = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'disable-all-custom-nodes' -and $_.CommandLine -match "$Port" } |
        Select-Object -First 1
    if (-not $managedAlive -and -not (Test-HttpOk -Url "http://127.0.0.1:$Port/system_stats")) {
        # Allow a few seconds for process spawn before declaring crash
        if (((Get-Date) - $started).TotalSeconds -gt 12 -and $ownersNow.Count -eq 0) {
            Write-StartupLog "ERROR_STATUS=CRASHED_DURING_STARTUP (no managed python)"
            Write-InstanceMarker -ProcessId $launcherPid -Executable $managedPython -Argv $argvJoined `
                -InstanceId $instanceId -Status "CRASHED"
            exit 7
        }
    }
    # Port listen alone is not enough
    if (Test-HttpOk -Url "http://127.0.0.1:$Port/system_stats") {
        if (-not $SkipHealthVerify) {
            if (Test-ComfyFullReady -RequireCheckpoint) {
                $ready = $true
                break
            }
        } else {
            $ready = $true
            break
        }
    }
    Start-Sleep -Milliseconds 750
}

if (-not $ready) {
    Write-StartupLog "ERROR_STATUS=STARTUP_TIMEOUT see $managedStderrLog"
    Write-InstanceMarker -ProcessId $launcherPid -Executable $managedPython -Argv $argvJoined `
        -InstanceId $instanceId -Status "TIMEOUT"
    if (-not $PortableFallback) {
        exit 8
    }
}

if ($ready) {
    $listenPid = $launcherPid
    $listenExe = $managedPython
    $liveOwners = @(Get-PortOwners -ListenPort $Port)
    $managedLive = $liveOwners | Where-Object { $_.Managed } | Select-Object -First 1
    if ($managedLive) {
        $listenPid = $managedLive.Pid
        if ($managedLive.Executable) { $listenExe = $managedLive.Executable }
    }
    Write-InstanceMarker -ProcessId $listenPid -Executable $listenExe -Argv $argvJoined `
        -InstanceId $instanceId -Status "READY"
    $elapsed = [int]((Get-Date) - $started).TotalSeconds
    Write-StartupLog "Managed Comfy READY on port $Port in ${elapsed}s pid=$listenPid"
    if ($Warmup) {
        if (-not (Invoke-ComfyWarmup)) {
            Write-StartupLog "ERROR_STATUS=WARMUP_FAILED"
            exit 9
        }
    }
    exit 0
}

# Portable fallback (explicit only)
if (-not (Test-Path -LiteralPath $portablePython) -or -not (Test-Path -LiteralPath $portableMain)) {
    throw "The explicit portable fallback is incomplete."
}
Write-StartupLog "Portable fallback launching (explicit)."
$argsPortable = @(
    "-s", "ComfyUI\main.py", "--windows-standalone-build",
    "--listen", "127.0.0.1", "--port", "$Port", "--lowvram",
    "--disable-all-custom-nodes",
    "--extra-model-paths-config", $sharedModelPaths,
    "--input-directory", $sharedInputDir,
    "--output-directory", $sharedOutputDir
)
Start-Process -FilePath $portablePython -ArgumentList $argsPortable `
    -WorkingDirectory $portableInstallDir -WindowStyle Hidden
exit 0
