param([string]$appRoot,[string]$testRoot)
$ErrorActionPreference='Stop'
. (Join-Path $appRoot 'scripts\AtnosOllamaRuntime.ps1')
$target=Join-Path $testRoot 'roles.env'
[IO.File]::WriteAllText($target, @'
OLLAMA_MAIN_MODEL="qwen3.6:27b-q4_K_M"
OLLAMA_CHAT_MODEL=qwen3.6:27b-q4_K_M
OLLAMA_DOCUMENT_MODEL=qwen3:8b # keep this independent
OPENAI_API_KEY=fixture-only-not-to-be-imported
OLLAMA_MODELS=C:\wrong
'@)
$beforeSecret=$env:OPENAI_API_KEY
$roles=Get-AtnosOllamaModelRoles -EnvFile $target
if($roles.OLLAMA_MAIN_MODEL -ne 'qwen3.6:27b-q4_K_M' -or $roles.OLLAMA_CHAT_MODEL -ne 'qwen3.6:27b-q4_K_M'){throw 'Configured role was overwritten'}
if($roles.OLLAMA_FAST_MODEL -ne 'qwen3.5:9b' -or $roles.OLLAMA_DOCUMENT_MODEL -ne 'qwen3:8b'){throw 'Unrelated role changed'}
if($roles.Contains('OPENAI_API_KEY') -or $roles.Contains('OLLAMA_MODELS') -or $env:OPENAI_API_KEY -ne $beforeSecret){throw 'Non-model setting was imported'}
[IO.File]::WriteAllText($target, 'OLLAMA_MAIN_MODEL=$(throw "must not execute")')
$rejected=$false
try { Get-AtnosOllamaModelRoles -EnvFile $target | Out-Null } catch { $rejected=$true }
if(-not $rejected){throw 'Unsafe value was not rejected'}
$defaults=Get-AtnosOllamaModelRoles -EnvFile (Join-Path $testRoot 'absent.env')
if($defaults.OLLAMA_MAIN_MODEL -ne 'qwen3:14b'){throw 'Default changed without configuration'}
Write-Output 'PASS: configured model roles; independent production models; no secrets or code evaluation.'
