# install.ps1 — Download and extract the Flyway CLI into ./flyway-cli/
# Run once from V2/:  .\install.ps1
# The flyway-cli/ directory is git-ignored.

param(
    [string]$Version = "10.15.0"
)

$ErrorActionPreference = "Stop"

$ZipName     = "flyway-commandline-$Version-windows-x64.zip"
$DownloadUrl = "https://repo1.maven.org/maven2/org/flywaydb/flyway-commandline/$Version/$ZipName"
$DestDir     = Join-Path $PSScriptRoot "flyway-cli"
$ZipPath     = Join-Path $PSScriptRoot $ZipName

if (Test-Path (Join-Path $DestDir "flyway.cmd")) {
    Write-Host "Flyway $Version already installed at $DestDir" -ForegroundColor Green
    exit 0
}

Write-Host "Downloading Flyway $Version ..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing

Write-Host "Extracting ..." -ForegroundColor Cyan
Expand-Archive -Path $ZipPath -DestinationPath $PSScriptRoot -Force

# The archive extracts to flyway-<version>/ — rename to flyway-cli/
$ExtractedDir = Join-Path $PSScriptRoot "flyway-$Version"
if (Test-Path $ExtractedDir) {
    if (Test-Path $DestDir) { Remove-Item $DestDir -Recurse -Force }
    Rename-Item -Path $ExtractedDir -NewName "flyway-cli"
}

Remove-Item $ZipPath -Force

Write-Host "Flyway $Version installed at $DestDir" -ForegroundColor Green
Write-Host "Run .\migrate.ps1 to apply pending migrations." -ForegroundColor Green
