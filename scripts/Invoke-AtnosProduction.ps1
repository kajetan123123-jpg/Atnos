param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ProductionArguments)
$ErrorActionPreference='Stop'
$env:USERPROFILE='E:\Atnos\Profiles\AtnosSystem'
$env:APPDATA='E:\Atnos\Profiles\AtnosSystem\AppData\Roaming'
$env:LOCALAPPDATA='E:\Atnos\Profiles\AtnosSystem\AppData\Local'
$env:TEMP='E:\Atnos\Temp'
$env:TMP=$env:TEMP
$env:PYTHONIOENCODING='utf-8'
$env:ATNOS_ENV_FILE='E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\config\.env'
$env:PYTHONPATH='E:\Atnos\App\runtime-recovered\api\src'
$env:HF_HOME='E:\Atnos\Storage\ToolCaches\huggingface'
$env:XDG_CACHE_HOME='E:\Atnos\Storage\ToolCaches'
$env:TORCH_HOME='E:\Atnos\Storage\ToolCaches\torch'
$env:NUMBA_CACHE_DIR='E:\Atnos\Storage\ToolCaches\numba'
& 'E:\Atnos\App\runtime-recovered\api\.venv\Scripts\python.exe' 'E:\Atnos\App\runtime-recovered\api\scripts\run_production_order.py' @ProductionArguments
exit $LASTEXITCODE
