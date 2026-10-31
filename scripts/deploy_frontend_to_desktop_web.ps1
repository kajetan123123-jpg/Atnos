param(
    [string]$RepoRoot = "",
    [switch]$Build
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $canonicalRepoRoot = "E:\Atnos\App"
    $RepoRoot = if (Test-Path -LiteralPath $canonicalRepoRoot -PathType Container) {
        $canonicalRepoRoot
    } else {
        (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
    }
} else {
    $RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
}

$frontendDir = Join-Path $RepoRoot "frontend"
$distDir = Join-Path $frontendDir "dist"
$desktopWebDir = Join-Path $RepoRoot "desktop-web"
$bundledNodeExe = "E:\Atnos\Storage\ToolCaches\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
$nodeExe = if (Test-Path -LiteralPath $bundledNodeExe -PathType Leaf) {
    $bundledNodeExe
} elseif ($nodeCommand) {
    $nodeCommand.Source
} else {
    throw "Node.js runtime is unavailable. Expected $bundledNodeExe or node.exe in PATH."
}

if ($Build) {
    Push-Location $frontendDir
    try {
        & $nodeExe .\scripts\verify-design-tokens.mjs
        if ($LASTEXITCODE -ne 0) { throw "Design token verification failed with exit code $LASTEXITCODE." }
        & $nodeExe .\node_modules\typescript\bin\tsc -b
        if ($LASTEXITCODE -ne 0) { throw "TypeScript build failed with exit code $LASTEXITCODE." }
        & $nodeExe .\node_modules\vite\bin\vite.js build
        if ($LASTEXITCODE -ne 0) { throw "Vite build failed with exit code $LASTEXITCODE." }
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path -LiteralPath $distDir -PathType Container)) {
    throw "Frontend dist directory does not exist: $distDir. Run frontend build first."
}

$resolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
$resolvedTargetParent = (Resolve-Path -LiteralPath (Split-Path -Parent $desktopWebDir)).Path
$resolvedTarget = if (Test-Path -LiteralPath $desktopWebDir) {
    (Resolve-Path -LiteralPath $desktopWebDir).Path
} else {
    $desktopWebDir
}

if (-not $resolvedTarget.StartsWith($resolvedRepo, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to deploy outside repo: $resolvedTarget"
}
if (-not $resolvedTargetParent.StartsWith($resolvedRepo, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to deploy outside repo parent: $resolvedTargetParent"
}

New-Item -ItemType Directory -Force -Path $desktopWebDir | Out-Null

# Keep immutable chunks used by already-open tabs. Copy new assets before
# publishing the entry point so readers never see an index with absent chunks.
$sourceIndex = Join-Path $distDir "index.html"
if (-not (Test-Path -LiteralPath $sourceIndex -PathType Leaf)) {
    throw "Frontend dist has no index.html; preserving the current deployment."
}
Get-ChildItem -LiteralPath $distDir -Force | Where-Object { $_.Name -ne "index.html" } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $desktopWebDir -Recurse -Force
}
$targetIndex = Join-Path $desktopWebDir "index.html"
$stagedIndex = Join-Path $desktopWebDir (".index.deploy-" + [guid]::NewGuid().ToString("N") + ".html")
try {
    Copy-Item -LiteralPath $sourceIndex -Destination $stagedIndex
    if (Test-Path -LiteralPath $targetIndex -PathType Leaf) {
        [System.IO.File]::Replace($stagedIndex, $targetIndex, [NullString]::Value)
    } else {
        [System.IO.File]::Move($stagedIndex, $targetIndex)
    }
} finally {
    if (Test-Path -LiteralPath $stagedIndex -PathType Leaf) {
        Remove-Item -LiteralPath $stagedIndex -Force
    }
}

Write-Host "Deployed frontend dist to desktop shell root:"
Write-Host $desktopWebDir
