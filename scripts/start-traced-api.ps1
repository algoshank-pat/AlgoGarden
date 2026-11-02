param()
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Create .env from .env.example and enter your LangSmith API key first.'
}
# Agent ID/environment are loaded from .env; no legacy project override.
Push-Location $projectRoot
try {
    uv run --env-file $envPath --extra voice uvicorn chat_api:app --host 127.0.0.1 --port 8010
} finally {
    Pop-Location
}
