$ErrorActionPreference = "Stop"

function Add-CargoToPath {
  $cargoHome = if ($env:CARGO_HOME) {
    $env:CARGO_HOME
  } else {
    Join-Path $env:USERPROFILE ".cargo"
  }
  $cargoBin = Join-Path $cargoHome "bin"

  if (Test-Path $cargoBin) {
    $env:Path = "$cargoBin;$env:Path"
  }
}

function Find-VsDevShell {
  $candidates = [System.Collections.Generic.List[string]]::new()
  $programFilesX86 = ${env:ProgramFiles(x86)}

  if ($programFilesX86) {
    $vswhere = Join-Path $programFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
      $installPath = & $vswhere `
        -latest `
        -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath `
        2>$null
      if ($installPath) {
        $candidates.Add((Join-Path $installPath "Common7\Tools\Launch-VsDevShell.ps1"))
      }
    }

    foreach ($edition in "BuildTools", "Community", "Professional", "Enterprise") {
      $candidates.Add(
        (Join-Path $programFilesX86 "Microsoft Visual Studio\2022\$edition\Common7\Tools\Launch-VsDevShell.ps1")
      )
    }
  }

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) {
      return $candidate
    }
  }

  return $null
}

function Test-LibraryOnPath([string]$libraryName) {
  if (-not $env:LIB) {
    return $false
  }

  foreach ($directory in $env:LIB -split ";") {
    if (-not $directory) {
      continue
    }

    if (Test-Path (Join-Path $directory $libraryName)) {
      return $true
    }
  }

  return $false
}

function Enter-WindowsBuildEnvironment {
  Add-CargoToPath

  $vsDevShell = Find-VsDevShell
  if (-not $vsDevShell) {
    throw "Visual Studio Build Tools with the Desktop C++ workload are required."
  }

  & $vsDevShell -Arch amd64 -HostArch amd64 | Out-Null

  if (-not (Get-Command link.exe -ErrorAction SilentlyContinue)) {
    throw "MSVC linker was not found after loading the Visual Studio developer shell."
  }

  if (-not (Test-LibraryOnPath "kernel32.lib")) {
    throw "Windows SDK libraries were not found after loading the Visual Studio developer shell."
  }
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $Root ".build\windows"
$DistDir = Join-Path $BuildRoot "dist"
$WorkDir = Join-Path $BuildRoot "work"
$DaemonBin = Join-Path $DistDir "hermes-node-daemon.exe"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm is required"
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "python is required"
}

Enter-WindowsBuildEnvironment

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
  throw "cargo is required"
}

Set-Location $Root

npm install
npm run check
python -m pip install --upgrade pip
python -m pip install -r daemon/requirements.txt pyinstaller
python -m py_compile daemon/hermes-node-daemon.py

if (Test-Path $BuildRoot) {
  Remove-Item $BuildRoot -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  daemon/hermes-node-daemon.py `
  --name hermes-node-daemon `
  --distpath $DistDir `
  --workpath $WorkDir `
  --specpath $WorkDir

node scripts/prepare-resources.mjs $DaemonBin
$TauriConfig = node scripts/generate-release-config.mjs
npm run tauri:build -- --config $TauriConfig.Trim()

if ($env:WINDOWS_SIGN_CERT_PATH -or $env:WINDOWS_SIGN_SUBJECT_NAME) {
  & (Join-Path $Root "scripts\sign-windows.ps1") -Root $Root
}

if ($env:HERMES_REQUIRE_SIGNED_RELEASE -eq "1") {
  & (Join-Path $Root "scripts\assert-windows-signatures.ps1") -Root $Root
}

if ($env:HERMES_RELEASE_BASE_URL) {
  node scripts/generate-release-manifest.mjs
}
