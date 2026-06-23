# Environment variables are loaded automatically from .env by every Python
# process here (see constants/env.py: load_env). No manual env setup needed —
# just keep .env populated.

# Start the API server in a new window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$PSScriptRoot'
    Write-Host 'Starting API server...' -ForegroundColor Cyan
    uvicorn api:app --host 0.0.0.0 --port 8000
" -WindowStyle Normal

# Give the API a moment to boot before starting the central node
Write-Host "Waiting for API to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 4

# Start the central node in a second new window — one format at a time, sequentially
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$PSScriptRoot'
    Write-Host 'Starting central node (commander runs on Fly.io)...' -ForegroundColor Green
    foreach (`$fmt in @('pauper','modern','legacy','vintage','highlanderCanadian')) {
        Write-Host `"=== Sweeping `$fmt ===`" -ForegroundColor Green
        python central_node.py --format `$fmt
    }
" -WindowStyle Normal

Write-Host "Done. API and central node are running in separate windows." -ForegroundColor White
