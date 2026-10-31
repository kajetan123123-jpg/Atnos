param([string]$appRoot)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $appRoot 'Launch-Atnos.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Launcher parse error'}
$main=$ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.TryStatementAst] } | Select-Object -Last 1
if(-not $main){throw 'No launcher entry point'}
function Acquire-LauncherMutex {param($TimeoutMs);return $launcherMutex.WaitOne(0)}
function Write-LauncherLog {param($Message)}
function Write-LauncherState {param($Phase)}
function Clear-LauncherState {}
function Ensure-OllamaRuntime {Write-Output 'PASS: models only; no application services or capability changes.'}
function Initialize-LocalCapabilitySession {throw 'Model-only startup changed capability'}
function Cleanup-OrphanedManagedServiceProcesses {throw 'Model-only startup cleaned services'}
function Ensure-Api {throw 'Model-only startup changed API'}
function Ensure-Web {throw 'Model-only startup changed web'}
function Ensure-FrontendDeployment {throw 'Model-only startup changed frontend'}
function Get-ApiStartupSummary {return 'fixture'}
$ModelsOnly=$true; $NoDesktopShell=$true; $NoErrorDialog=$true
$launcherMutex=[Threading.Mutex]::new()
# No processes are invoked by this fixture. Execute the actual entry-point
# control flow with forbidden service mutations replaced by throwing stubs.
Invoke-Expression $main.Extent.Text
