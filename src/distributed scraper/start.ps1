# Load environment variables from .env
Get-Content "$PSScriptRoot\.env" | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
    }
}

# Start the API server in a new window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$PSScriptRoot'
    `$env:DATABASE_URL = '$env:DATABASE_URL'
    `$env:API_KEY      = '$env:API_KEY'
    Write-Host 'Starting API server...' -ForegroundColor Cyan
    uvicorn api:app --host 0.0.0.0 --port 8000
" -WindowStyle Normal

# Give the API a moment to boot before starting the central node
Write-Host "Waiting for API to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 4

# Start the central node in a second new window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$PSScriptRoot'
    `$env:API_KEY          = '$env:API_KEY'
    `$env:SCRAPER_API_URL  = '$env:SCRAPER_API_URL'
    Write-Host 'Starting central node...' -ForegroundColor Green
    python central_node.py
" -WindowStyle Normal

Write-Host "Done. API and central node are running in separate windows." -ForegroundColor White
