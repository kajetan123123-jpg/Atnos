$ErrorActionPreference = 'Stop'
$requiredCwd = 'E:\Atnos\App'
function Get-WorkspaceHash([string]$Path) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($Path))).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose() }
}
$expectedNames = @('atnos-capability-runtime', 'atnos-evidence-and-data', 'atnos-final-review',
    'atnos-implementation-strategy', 'atnos-integration-builder', 'atnos-remotion-production',
    'atnos-security-review', 'atnos-systematic-debugging', 'atnos-verification-before-completion',
    'atnos-windows-runtime-debug')
$result = [ordered]@{
    checkedAtUtc = [DateTime]::UtcNow.ToString('o'); verificationType = 'filesystem_workspace_preflight'
    cwd = (Get-Location).ProviderPath; requiredCwd = $requiredCwd; passed = $false
    instructions = @(); skills = @(); nativeSkillDiscoveryVerified = $false; errors = @()
}
if (-not [string]::Equals($result.cwd, $requiredCwd, [StringComparison]::OrdinalIgnoreCase)) {
    $result.errors += "Codex Atnosa wymaga rzeczywistego cwd $requiredCwd; obecne cwd: $($result.cwd)."
}
try {
    foreach ($instructionPath in @('E:\AGENTS.md', 'E:\Atnos\App\AGENTS.md')) {
        if (-not (Test-Path -LiteralPath $instructionPath -PathType Leaf)) {
            $result.errors += "Brak instrukcji: $instructionPath"; continue
        }
        if ([string]::IsNullOrWhiteSpace([IO.File]::ReadAllText($instructionPath))) {
            $result.errors += "Puste instrukcje: $instructionPath"; continue
        }
        $result.instructions += @{ path = $instructionPath; sha256 = Get-WorkspaceHash $instructionPath }
    }
    $skillsRoot = Join-Path $requiredCwd '.agents\skills'
    if (-not (Test-Path -LiteralPath $skillsRoot -PathType Container)) { throw "Brak katalogu skills: $skillsRoot" }
    $skillFiles = @(Get-ChildItem -LiteralPath $skillsRoot -Filter 'SKILL.md' -File -Recurse -Force)
    if ($skillFiles.Count -ne 10) { $result.errors += "Wymagane dokładnie 10 SKILL.md; znaleziono $($skillFiles.Count)." }
    foreach ($expectedName in $expectedNames) {
        $skillPath = Join-Path $skillsRoot "$expectedName\SKILL.md"
        if (-not (Test-Path -LiteralPath $skillPath -PathType Leaf)) {
            $result.errors += "Brak skillu: $expectedName"; continue
        }
        $text = [IO.File]::ReadAllText($skillPath)
        $frontmatter = [regex]::Match($text, '\A---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)')
        $name = [regex]::Match($frontmatter.Groups[1].Value, '(?m)^name:\s*([a-z0-9-]+)\s*$')
        $description = [regex]::Match($frontmatter.Groups[1].Value, '(?m)^description:\s*(.+?)\s*$').Groups[1].Value.Trim()
        if ($description.StartsWith('"')) {
            try { $description = ConvertFrom-Json -InputObject $description -ErrorAction Stop }
            catch { $description = $null }
        } elseif ($description.StartsWith("'")) {
            if ($description -match "^'(?:[^']|'')*'$" ) { $description = $description.Substring(1, $description.Length - 2).Replace("''", "'") }
            else { $description = $null }
        }
        if (-not $frontmatter.Success -or $name.Groups[1].Value -cne $expectedName -or
            $description -isnot [string] -or [string]::IsNullOrWhiteSpace($description) -or $description -match '^[>|]') {
            $result.errors += "Nieprawidłowe name/description w frontmatterze: $skillPath"; continue
        }
        $result.skills += @{ name = $expectedName; path = $skillPath; sha256 = Get-WorkspaceHash $skillPath }
    }
    foreach ($skillFile in $skillFiles) {
        $expectedPath = Join-Path $skillsRoot "$($skillFile.Directory.Name)\SKILL.md"
        if ($skillFile.Directory.Name -cnotin $expectedNames -or $skillFile.FullName -ine $expectedPath) {
            $result.errors += "Nieoczekiwany SKILL.md: $($skillFile.FullName)"
        }
    }
} catch { $result.errors += $_.Exception.Message }
$result.passed = $result.errors.Count -eq 0
$result | ConvertTo-Json -Depth 5
if ($result.passed) { exit 0 } else { exit 1 }
