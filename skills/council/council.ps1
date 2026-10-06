# council.ps1 — wrapper do Hermes Council para Windows (PowerShell)
# Uso:
#   $env:COUNCIL_PROFILE = "<perfil>"     # ou $env:HERMES_HOME = "$env:LOCALAPPDATA\hermes\profiles\<perfil>"
#   .\council.ps1 "sua pergunta" --members 4
Set-Location -Path $PSScriptRoot
$env:PYTHONUTF8 = "1"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv run python council.py @args
} else {
    py council.py @args
}
exit $LASTEXITCODE
