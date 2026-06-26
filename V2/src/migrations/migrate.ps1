# migrate.ps1 — Run flyway migrate for the v2 schema.
# Run from V2/:  .\migrate.ps1
#
# Environment variables (set before running, or edit flyway.toml defaults):
#   FLYWAY_PASSWORD  — Postgres password (required if not the default)
#   FLYWAY_URL       — Override the JDBC URL from flyway.toml
#   FLYWAY_USER      — Override the DB user from flyway.toml

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

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
Write-Host "`nRunning flyway migrate (v2 schema) ..." -ForegroundColor Cyan
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
