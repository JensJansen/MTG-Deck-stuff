# migrate.ps1 — Lint migration files then run flyway migrate.
# Run from src/database/:  .\migrate.ps1
#
# Environment variables (set before running, or edit flyway.toml defaults):
#   FLYWAY_DB_PASSWORD  — Postgres password (required if not the default)
#   FLYWAY_URL          — Override the JDBC URL from flyway.toml
#   FLYWAY_USER         — Override the DB user from flyway.toml

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

# ---------------------------------------------------------------------------
# Lint: block any migration containing destructive DDL
# ---------------------------------------------------------------------------
$Forbidden = @("DROP TABLE", "DROP SCHEMA", "DROP COLUMN", "TRUNCATE")
$MigrationsDir = Join-Path $ScriptDir "migrations"
$violations = @()

Get-ChildItem -Path $MigrationsDir -Filter "*.sql" | ForEach-Object {
    $lines = Get-Content $_.FullName | Where-Object { $_ -notmatch '^\s*--' }
    $content = $lines -join "`n"
    foreach ($keyword in $Forbidden) {
        if ($content -imatch [regex]::Escape($keyword)) {
            $violations += "$($_.Name) contains forbidden keyword: $keyword"
        }
    }
}

if ($violations.Count -gt 0) {
    Write-Host "`nMigration lint FAILED — destructive DDL detected:" -ForegroundColor Red
    $violations | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host "`nMigrations must be additive only. Remove the offending statements and retry." -ForegroundColor Red
    exit 1
}

Write-Host "Lint passed — no destructive DDL found." -ForegroundColor Green

# ---------------------------------------------------------------------------
# Locate Flyway CLI
# ---------------------------------------------------------------------------
$FlywayCmd = Join-Path $ScriptDir "flyway-cli\flyway.cmd"
if (-not (Test-Path $FlywayCmd)) {
    Write-Host "`nFlyway CLI not found. Run .\install.ps1 first." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Run flyway migrate
# Config is read from flyway.toml in this directory.
# ---------------------------------------------------------------------------
Write-Host "`nRunning flyway migrate ..." -ForegroundColor Cyan
Push-Location $ScriptDir
try {
    & $FlywayCmd migrate
} finally {
    Pop-Location
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "`nflyway migrate failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`nMigrations applied successfully." -ForegroundColor Green
