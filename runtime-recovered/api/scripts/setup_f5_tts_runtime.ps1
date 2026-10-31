 [CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InstallDir = "E:\Atnos\Tools\F5-TTS-runtime",
    [string]$PythonPath = "python",
    [string]$FFmpegBinDir = "E:\Atnos\Tools\FFmpegShared\bin",
    [string]$BackendEnvPath = "",
    [switch]$UpdateEnv
)

$ErrorActionPreference = "Stop"

function Set-EnvLine {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    $line = "$Key=$Value"
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType File -Path $Path -Force | Out-Null
        Set-Content -LiteralPath $Path -Value $line -Encoding UTF8
        return
    }
    $content = Get-Content -LiteralPath $Path -Raw
    if ($content -match "(?m)^$([regex]::Escape($Key))=") {
        $content = [regex]::Replace($content, "(?m)^$([regex]::Escape($Key))=.*$", $line)
        Set-Content -LiteralPath $Path -Value $content -Encoding UTF8
        return
    }
    Add-Content -LiteralPath $Path -Value $line -Encoding UTF8
}

$installPath = [System.IO.Path]::GetFullPath($InstallDir)
$venvPath = Join-Path $installPath ".venv"
$runtimePython = Join-Path $venvPath "Scripts\python.exe"

if ($PythonPath -eq "python") {
    try {
        $preferredPython = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $preferredPython) {
            $PythonPath = $preferredPython.Trim()
            Write-Host "[Atnos] Using Python 3.12 for F5-TTS runtime: $PythonPath"
        }
    } catch {
        Write-Host "[Atnos] Python 3.12 launcher not available; using $PythonPath"
    }
}

Write-Host "[Atnos] Preparing F5-TTS runtime at $installPath"
if ($PSCmdlet.ShouldProcess($installPath, "Create F5-TTS runtime directory")) {
    New-Item -ItemType Directory -Path $installPath -Force | Out-Null
}

if (-not (Test-Path -LiteralPath $runtimePython)) {
    Write-Host "[Atnos] Creating isolated Python venv"
    if ($PSCmdlet.ShouldProcess($venvPath, "Create isolated Python venv")) {
        & $PythonPath -m venv $venvPath
    }
}

Write-Host "[Atnos] Upgrading installer tooling"
if ($PSCmdlet.ShouldProcess($runtimePython, "Install or upgrade pip tooling")) {
    & $runtimePython -m pip install --upgrade pip wheel setuptools
}

Write-Host "[Atnos] Installing F5-TTS runtime packages"
if ($PSCmdlet.ShouldProcess($runtimePython, "Install F5-TTS and Gradio packages")) {
    & $runtimePython -m pip install "f5-tts==1.1.20" gradio
}

Write-Host "[Atnos] Verifying imports"
if ($PSCmdlet.ShouldProcess($runtimePython, "Verify F5-TTS runtime imports")) {
    & $runtimePython -c "import f5_tts, gradio; print('F5-TTS runtime OK')"
}

$runtimeGradio = Join-Path $venvPath "Scripts\f5-tts_infer-gradio.exe"
$launchCommand = "set PATH=$FFmpegBinDir;%PATH% && $runtimeGradio -a -H 127.0.0.1 -p 7860"

if ($UpdateEnv) {
    if (-not $BackendEnvPath) {
        $BackendEnvPath = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
    }
    Write-Host "[Atnos] Updating $BackendEnvPath"
    if ($PSCmdlet.ShouldProcess($BackendEnvPath, "Update F5-TTS env values")) {
        Set-EnvLine -Path $BackendEnvPath -Key "F5_TTS_INSTALL_DIR" -Value $installPath
        Set-EnvLine -Path $BackendEnvPath -Key "F5_TTS_RUNTIME_PYTHON" -Value $runtimePython
        Set-EnvLine -Path $BackendEnvPath -Key "F5_TTS_LAUNCH_COMMAND" -Value $launchCommand
    }
}

Write-Host ""
Write-Host "[Atnos] Add these env values if you did not use -UpdateEnv:"
Write-Host "F5_TTS_INSTALL_DIR=$installPath"
Write-Host "F5_TTS_RUNTIME_PYTHON=$runtimePython"
Write-Host "F5_TTS_LAUNCH_COMMAND=$launchCommand"
Write-Host ""
Write-Host "[Atnos] Start command:"
Write-Host $launchCommand
