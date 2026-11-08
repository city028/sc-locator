# Builds dist\sc-locator-setup.exe, the one file a release attaches.
# Needs PyInstaller, Tesseract's language data (see sc_locator.spec) and
# Inno Setup (winget install JRSoftware.InnoSetup). Run with:
#   powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (Select-String -Path updater.py -Pattern '^VERSION = "(.+?)"').Matches[0].Groups[1].Value

$iscc = Get-ChildItem "${env:ProgramFiles(x86)}\Inno Setup *\ISCC.exe", "$env:ProgramFiles\Inno Setup *\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup *\ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $iscc) { throw "ISCC.exe not found: winget install JRSoftware.InnoSetup" }

python -m PyInstaller --noconfirm sc_locator.spec
if ($LASTEXITCODE) { exit $LASTEXITCODE }

& $iscc.FullName "/DAppVersion=$version" installer\sc-locator.iss
if ($LASTEXITCODE) { exit $LASTEXITCODE }

Write-Host "Built dist\sc-locator-setup.exe, version $version"
