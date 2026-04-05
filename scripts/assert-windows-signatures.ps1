$ErrorActionPreference = "Stop"

param(
  [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$bundleRoot = Join-Path $Root "src-tauri\target\release\bundle"
$artifacts = @()
$artifacts += Get-ChildItem -Path (Join-Path $bundleRoot "nsis") -Filter *.exe -ErrorAction SilentlyContinue
$artifacts += Get-ChildItem -Path (Join-Path $bundleRoot "msi") -Filter *.msi -ErrorAction SilentlyContinue

if ($artifacts.Count -eq 0) {
  throw "No Windows release artifacts were found to verify."
}

$failures = @()

foreach ($artifact in $artifacts) {
  $signature = Get-AuthenticodeSignature $artifact.FullName
  if ($signature.Status -ne "Valid") {
    $failures += "$($artifact.FullName): $($signature.Status) - $($signature.StatusMessage)"
  }
}

if ($failures.Count -gt 0) {
  $message = "Unsigned or invalid Windows artifacts were found:`n" + ($failures -join "`n")
  throw $message
}

Write-Host "All Windows release artifacts have valid signatures."
