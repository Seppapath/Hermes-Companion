param(
  [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

function Resolve-SignTool {
  $command = Get-Command signtool -ErrorAction SilentlyContinue
  if ($command) {
    return $command.Source
  }

  throw "signtool is required. Run this from a Developer PowerShell or install the Windows SDK."
}

function Sign-Artifact {
  param(
    [string]$SignTool,
    [string]$Path
  )

  if (-not (Test-Path $Path)) {
    return
  }

  $timestampUrl = if ($env:WINDOWS_TIMESTAMP_URL) { $env:WINDOWS_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
  $arguments = @("sign", "/fd", "SHA256", "/tr", $timestampUrl, "/td", "SHA256")

  if ($env:WINDOWS_SIGN_CERT_PATH) {
    $arguments += @("/f", $env:WINDOWS_SIGN_CERT_PATH)
    if ($env:WINDOWS_SIGN_CERT_PASSWORD) {
      $arguments += @("/p", $env:WINDOWS_SIGN_CERT_PASSWORD)
    }
  } elseif ($env:WINDOWS_SIGN_SUBJECT_NAME) {
    $arguments += @("/n", $env:WINDOWS_SIGN_SUBJECT_NAME)
  } else {
    throw "Set WINDOWS_SIGN_CERT_PATH or WINDOWS_SIGN_SUBJECT_NAME before signing Windows artifacts."
  }

  & $SignTool @arguments $Path
  & $SignTool verify /pa $Path
}

$signTool = Resolve-SignTool
$bundleRoot = Join-Path $Root "src-tauri\target\release\bundle"

$artifacts = @()
$artifacts += Get-ChildItem -Path (Join-Path $bundleRoot "nsis") -Filter *.exe -ErrorAction SilentlyContinue
$artifacts += Get-ChildItem -Path (Join-Path $bundleRoot "msi") -Filter *.msi -ErrorAction SilentlyContinue

if ($artifacts.Count -eq 0) {
  throw "No Windows release artifacts were found to sign."
}

foreach ($artifact in $artifacts) {
  Sign-Artifact -SignTool $signTool -Path $artifact.FullName
}
